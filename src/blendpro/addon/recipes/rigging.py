"""Rigging & animation recipes (humanoid rigs for Unity/glTF export, procedural animation, baking).

Blender 4.4+/5.x layered actions are handled via _fcurves()/_channelbag() (action.fcurves no longer exists).
Bone names can be given either literally or as canonical keys (hips, spine, chest, upper_chest, neck, head,
shoulder_L, upper_arm_L, forearm_L, hand_L, thigh_L, shin_L, foot_L, toe_L, ..._R) — resolved per convention.
"""
import math
from contextlib import contextmanager

import bpy
import mathutils
from mathutils import Matrix, Quaternion, Vector

from . import recipe
from ..util import find_view3d, get_obj, r

# --------------------------------------------------------------------------------------------- naming
_CENTER = ["hips", "spine", "chest", "upper_chest", "neck", "head"]
_SIDE = ["shoulder", "upper_arm", "forearm", "hand", "thigh", "shin", "foot", "toe"]
CANON = _CENTER + [f"{p}_{s}" for s in "LR" for p in _SIDE]

_BLENDER = {"hips": "Hips", "spine": "Spine", "chest": "Chest", "upper_chest": "UpperChest", "neck": "Neck",
            "head": "Head", "shoulder": "Shoulder", "upper_arm": "UpperArm", "forearm": "ForeArm", "hand": "Hand",
            "thigh": "Thigh", "shin": "Shin", "foot": "Foot", "toe": "Toe"}
_MIXAMO = {"hips": "Hips", "spine": "Spine", "chest": "Spine1", "upper_chest": "Spine2", "neck": "Neck",
           "head": "Head", "shoulder": "Shoulder", "upper_arm": "Arm", "forearm": "ForeArm", "hand": "Hand",
           "thigh": "UpLeg", "shin": "Leg", "foot": "Foot", "toe": "ToeBase"}
_UNITY = {"hips": "Hips", "spine": "Spine", "chest": "Chest", "upper_chest": "UpperChest", "neck": "Neck",
          "head": "Head", "shoulder": "Shoulder", "upper_arm": "UpperArm", "forearm": "LowerArm", "hand": "Hand",
          "thigh": "UpperLeg", "shin": "LowerLeg", "foot": "Foot", "toe": "Toes"}
CONVENTIONS = ("blender", "mixamo", "unity")


def bone_name(canon, naming="blender"):
    """Canonical key -> bone name in a convention."""
    if naming not in CONVENTIONS:
        raise ValueError(f"naming must be one of {CONVENTIONS}, got {naming!r}")
    side = None
    base = canon
    if canon[-2:] in ("_L", "_R"):
        base, side = canon[:-2], canon[-1]
    if naming == "blender":
        return _BLENDER[base] + (f".{side}" if side else "")
    tbl = _MIXAMO if naming == "mixamo" else _UNITY
    nm = (("Left" if side == "L" else "Right") if side else "") + tbl[base]
    return "mixamorig:" + nm if naming == "mixamo" else nm


_REVERSE = {}
for _conv in CONVENTIONS:
    for _c in CANON:
        _REVERSE[bone_name(_c, _conv).lower()] = _c
for _c in CANON:
    _REVERSE[_c.lower()] = _c
# common extras (Rigify-ish / generic)
_REVERSE.update({"pelvis": "hips", "root_hips": "hips", "upperarm.l": "upper_arm_L", "upperarm.r": "upper_arm_R",
                 "upper_arm.l": "upper_arm_L", "upper_arm.r": "upper_arm_R", "forearm.l": "forearm_L",
                 "forearm.r": "forearm_R", "thigh.l": "thigh_L", "thigh.r": "thigh_R", "shin.l": "shin_L",
                 "shin.r": "shin_R", "hand.l": "hand_L", "hand.r": "hand_R", "foot.l": "foot_L", "foot.r": "foot_R",
                 "shoulder.l": "shoulder_L", "shoulder.r": "shoulder_R", "toe.l": "toe_L", "toe.r": "toe_R"})


def canon_of(name):
    """Bone name in any supported convention -> canonical key (or None)."""
    n = name.lower()
    if n in _REVERSE:
        return _REVERSE[n]
    for pre in ("mixamorig:", "mixamorig_", "mixamorig1:", "mixamorig2:", "armature|", "def-"):
        if n.startswith(pre) and ("mixamorig:" + n[len(pre):]) in _REVERSE:
            return _REVERSE["mixamorig:" + n[len(pre):]]
        if n.startswith(pre) and n[len(pre):] in _REVERSE:
            return _REVERSE[n[len(pre):]]
    n2 = n.replace("_l", ".l").replace("_r", ".r") if n.endswith(("_l", "_r")) else n
    return _REVERSE.get(n2)


def detect_naming(arm_obj):
    names = [b.name for b in arm_obj.data.bones]
    scores = {c: sum(1 for k in CANON if bone_name(k, c) in names) for c in CONVENTIONS}
    best = max(scores, key=scores.get)
    return best if scores[best] else None


def canon_map(arm_obj):
    """{canonical: actual bone name} for an armature."""
    out = {}
    for b in arm_obj.data.bones:
        c = canon_of(b.name)
        if c and c not in out:
            out[c] = b.name
    return out


def resolve_bone(arm_obj, name):
    """Accept a literal bone name or a canonical key."""
    if name in arm_obj.data.bones:
        return name
    cm = canon_map(arm_obj)
    c = canon_of(name) or name
    if c in cm:
        return cm[c]
    import difflib
    close = difflib.get_close_matches(name, [b.name for b in arm_obj.data.bones], n=5, cutoff=0.4)
    raise ValueError(f"Bone {name!r} not found in {arm_obj.name!r}. Close: {close}")


# --------------------------------------------------------------------------------------------- helpers
def _armature(name):
    obj = get_obj(name)
    if obj.type != "ARMATURE":
        raise ValueError(f"{name!r} is a {obj.type}, expected an ARMATURE")
    return obj


def _mesh(name):
    obj = get_obj(name)
    if obj.type != "MESH":
        raise ValueError(f"{name!r} is a {obj.type}, expected a MESH")
    return obj


def _link(obj, collection=None):
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def _ensure_object_mode():
    o = bpy.context.view_layer.objects.active
    if o is not None and o.mode != "OBJECT":
        ov = dict(find_view3d())
        ov.update(active_object=o, object=o)
        with bpy.context.temp_override(**ov):
            bpy.ops.object.mode_set(mode="OBJECT")


@contextmanager
def _mode(obj, mode="EDIT"):
    """Enter EDIT/POSE on obj with a proper override; always returns to OBJECT mode."""
    _ensure_object_mode()
    hidden = obj.hide_get(), obj.hide_viewport
    obj.hide_set(False)
    obj.hide_viewport = False
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    ov = dict(find_view3d())
    ov.update(active_object=obj, object=obj, selected_objects=[obj], selected_editable_objects=[obj])
    with bpy.context.temp_override(**ov):
        bpy.ops.object.mode_set(mode=mode)
    try:
        yield obj.data
    finally:
        with bpy.context.temp_override(**ov):
            bpy.ops.object.mode_set(mode="OBJECT")
        obj.hide_set(hidden[0])
        obj.hide_viewport = hidden[1]
        if prev_active is not None and prev_active.name in bpy.context.view_layer.objects:
            bpy.context.view_layer.objects.active = prev_active


def _channelbag(id_owner, create=False):
    """Channelbag (layered action) for the datablock's assigned slot, or None."""
    ad = id_owner.animation_data
    if ad is None or ad.action is None:
        return None
    act = ad.action
    slot = ad.action_slot
    if slot is None:
        return None
    for layer in act.layers:
        for strip in layer.strips:
            cb = strip.channelbag(slot)
            if cb is not None:
                return cb
    if create:
        from bpy_extras import anim_utils
        return anim_utils.action_ensure_channelbag_for_slot(act, slot)
    return None


def _fcurves(id_owner):
    ad = getattr(id_owner, "animation_data", None)
    if ad is None or ad.action is None:
        return []
    act = ad.action
    if hasattr(act, "fcurves"):  # legacy API (<4.4)
        return list(act.fcurves)
    cb = _channelbag(id_owner)
    return list(cb.fcurves) if cb else []


def _all_fcurves_of_action(act):
    if hasattr(act, "fcurves"):
        return list(act.fcurves)
    out = []
    for layer in act.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                out.extend(cb.fcurves)
    return out


def _bone_of_path(dp):
    if dp.startswith('pose.bones["'):
        return dp.split('"')[1]
    return None


def _new_action(owner, name):
    owner.animation_data_create()
    act = bpy.data.actions.new(name)
    owner.animation_data.action = act
    return act


def _ensure_action(owner, name):
    owner.animation_data_create()
    if owner.animation_data.action is None:
        return _new_action(owner, name)
    return owner.animation_data.action


def _rot_local(pb, axis_world, angle):
    """Quaternion (bone-local) for a rotation of `angle` around an armature-space axis."""
    R = pb.bone.matrix_local.to_3x3()
    ax = (R.inverted() @ Vector(axis_world)).normalized()
    return Quaternion(ax, angle)


def _q_world(pb, q_world):
    """Armature-space rotation quaternion -> bone-local basis quaternion (FK, ignoring parent pose)."""
    R = pb.bone.matrix_local.to_quaternion()
    return R.inverted() @ q_world @ R


def _vec_local(pb, v_world):
    return pb.bone.matrix_local.to_3x3().inverted() @ Vector(v_world)


def _key_q(pb, q, frame):
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = q
    pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)


def _cyclic(fcurves):
    n = 0
    for fc in fcurves:
        if not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")
            n += 1
    return n


def _deform_bones(arm_obj):
    return [b for b in arm_obj.data.bones if b.use_deform]


def _seg_dist(p, a, b):
    ab = b - a
    t = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length


# --------------------------------------------------------------------------------------------- demos
def _demo_rig(name="DemoRig", naming="blender", height=1.8):
    return biped_armature(name=name, naming=naming, height=height)["armature"]


def _demo_character(name="DemoCharacter", height=1.8):
    """Low-poly humanoid mesh (separate capsule-ish islands) in T-pose facing -Y."""
    import bmesh
    H = height
    bm = bmesh.new()

    def cyl(p0, p1, rad, segs=10):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
        m = Matrix.Translation((p0 + p1) / 2) @ rot
        bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=rad, radius2=rad, depth=d.length, matrix=m)

    cyl((0, 0, 0.50 * H), (0, 0, 0.82 * H), 0.10 * H)                       # torso
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.065 * H,
                              matrix=Matrix.Translation((0, 0, 0.92 * H)))  # head
    cyl((0, 0, 0.80 * H), (0, 0, 0.87 * H), 0.03 * H, 8)                    # neck
    for s in (1, -1):
        cyl((s * 0.09 * H, 0, 0.80 * H), (s * 0.50 * H, 0, 0.80 * H), 0.03 * H, 8)   # arm
        cyl((s * 0.06 * H, 0, 0.52 * H), (s * 0.06 * H, 0, 0.03 * H), 0.045 * H, 8)  # leg
        cyl((s * 0.06 * H, 0.02 * H, 0.015 * H), (s * 0.06 * H, -0.12 * H, 0.015 * H), 0.025 * H, 6)  # foot
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return _link(bpy.data.objects.new(name, me))


# --------------------------------------------------------------------------------------------- rig building
_BIPED = {  # canonical: (head, tail, parent, connected) in units of height, character faces -Y, left = +X
    "hips": ((0, 0, .53), (0, 0, .58), None, False),
    "spine": ((0, 0, .58), (0, 0, .66), "hips", True),
    "chest": ((0, 0, .66), (0, 0, .74), "spine", True),
    "upper_chest": ((0, 0, .74), (0, 0, .82), "chest", True),
    "neck": ((0, 0, .82), (0, 0, .87), "upper_chest", True),
    "head": ((0, 0, .87), (0, 0, 1.0), "neck", True),
    "shoulder_L": ((.02, 0, .80), (.10, 0, .80), "upper_chest", False),
    "upper_arm_L": ((.10, 0, .80), (.27, .01, .80), "shoulder_L", True),
    "forearm_L": ((.27, .01, .80), (.42, 0, .80), "upper_arm_L", True),
    "hand_L": ((.42, 0, .80), (.50, 0, .80), "forearm_L", True),
    "thigh_L": ((.06, 0, .51), (.06, -.01, .28), "hips", False),
    "shin_L": ((.06, -.01, .28), (.06, .01, .045), "thigh_L", True),
    "foot_L": ((.06, .01, .045), (.06, -.08, .012), "shin_L", True),
    "toe_L": ((.06, -.08, .012), (.06, -.12, .012), "foot_L", True),
}
for _k in list(_BIPED):
    if _k.endswith("_L"):
        h, t, p, c = _BIPED[_k]
        _BIPED[_k[:-2] + "_R"] = ((-h[0], h[1], h[2]), (-t[0], t[1], t[2]), p[:-2] + "_R" if p.endswith("_L") else p, c)


@recipe("rigging", tags=("armature", "humanoid", "unity", "mixamo", "biped", "rig"))
def biped_armature(name="Rig", naming="blender", height=1.8, fit_to=None, location=(0, 0, 0), show_in_front=True) -> dict:
    '''Create a 22-bone humanoid T-pose armature (Hips..Head, arms, legs, toes) that maps to Unity Humanoid.
    naming: 'blender' (UpperArm.L), 'mixamo' (mixamorig:LeftArm) or 'unity' (LeftUpperArm). fit_to=mesh name scales
    the rig to the mesh's bounding-box height and centres it on its footprint (mesh should face -Y, T-pose).'''
    if naming not in CONVENTIONS:
        raise ValueError(f"naming must be one of {CONVENTIONS}")
    H = float(height)
    off = Vector(location)
    if fit_to:
        from ..util import world_bbox
        mn, mx = world_bbox(_mesh(fit_to))
        H = mx.z - mn.z
        off = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))
    if H <= 0:
        raise ValueError("height must be > 0")
    arm = bpy.data.armatures.new(name)
    obj = _link(bpy.data.objects.new(name, arm))
    obj.location = off
    obj.show_in_front = show_in_front
    with _mode(obj, "EDIT"):
        ebs = {}
        for k, (h, t, p, c) in _BIPED.items():
            eb = arm.edit_bones.new(bone_name(k, naming))
            eb.head = Vector(h) * H
            eb.tail = Vector(t) * H
            ebs[k] = eb
        for k, (h, t, p, c) in _BIPED.items():
            if p:
                ebs[k].parent = ebs[p]
                ebs[k].use_connect = c
        for k, eb in ebs.items():  # consistent rolls: limbs Z-up / legs Z-forward
            if "arm" in k or "hand" in k or "shoulder" in k:
                eb.align_roll(Vector((0, 0, 1)))
            elif any(s in k for s in ("thigh", "shin", "foot", "toe")):
                eb.align_roll(Vector((0, -1, 0)))
    return {"armature": obj.name, "bones": len(arm.bones), "naming": naming, "height": r(H),
            "root": bone_name("hips", naming), "sample": [bone_name(k, naming) for k in ("hips", "upper_arm_L", "thigh_R")]}


_MP_NAMES = ["nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye", "right_eye_outer",
             "left_ear", "right_ear", "mouth_left", "mouth_right", "left_shoulder", "right_shoulder", "left_elbow",
             "right_elbow", "left_wrist", "right_wrist", "left_pinky", "right_pinky", "left_index", "right_index",
             "left_thumb", "right_thumb", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle",
             "right_ankle", "left_heel", "right_heel", "left_foot_index", "right_foot_index"]


def _to_vec(v, coords, scale):
    if v is None:
        return None
    if isinstance(v, dict):
        if v.get("visibility", 1.0) is not None and v.get("visibility", 1.0) < 0:
            return None
        v = (v.get("x"), v.get("y"), v.get("z"))
    try:
        x, y, z = (float(a) for a in list(v)[:3])
    except (TypeError, ValueError):
        return None
    if any(math.isnan(a) or math.isinf(a) for a in (x, y, z)):
        return None
    if coords == "mediapipe":      # x right, y down, z away from camera -> Blender Z-up, facing -Y
        x, y, z = x, z, -y
    elif coords == "yup":          # generic Y-up (x right, y up, z toward viewer)
        x, y, z = x, -z, y
    elif coords != "blender":
        raise ValueError("coords must be 'blender', 'mediapipe' or 'yup'")
    return Vector((x, y, z)) * scale


def _mediapipe_skeleton(lm, min_visibility):
    """MediaPipe landmarks (33-list or name dict) -> (joints, parents, tails) with canonical keys."""
    if isinstance(lm, (list, tuple)):
        if len(lm) < 33:
            raise ValueError(f"MediaPipe list needs 33 landmarks, got {len(lm)}")
        lm = {n: lm[i] for i, n in enumerate(_MP_NAMES)}
    pts = {}
    for k, v in lm.items():
        vis = None
        if isinstance(v, dict):
            vis = v.get("visibility")
        elif isinstance(v, (list, tuple)) and len(v) >= 4:
            vis = v[3]
        if vis is not None and vis < min_visibility:
            continue
        pts[k.lower()] = v
    return pts


@recipe("rigging", tags=("armature", "mediapipe", "pose", "joints", "mocap", "skeleton"))
def armature_from_joint_positions(joints, parents=None, name="JointRig", preset=None, coords="blender", scale=1.0,
                                  naming="blender", tails=None, connect=False, min_length=0.01, location=(0, 0, 0),
                                  ground=False, min_visibility=0.3) -> dict:
    '''Build an armature from joint positions: one bone per joint, head at the joint, tail at its child.
    Generic: joints={name:[x,y,z]}, parents={child:parent|None} (missing -> root), optional tails={name:[x,y,z]}.
    preset='mediapipe': joints = 33-item landmark list ([x,y,z(,vis)]) or {"left_shoulder":[..],...} (WORLD landmarks,
    use coords='mediapipe'); builds a canonical humanoid (hips/spine/chest/neck/head, arms, legs, feet) named per `naming`.
    Robust: NaN/None/low-visibility joints are skipped (children re-parented), unknown parents -> root, cycles broken,
    zero-length bones nudged to min_length. coords: blender | mediapipe (x,y-down,z) | yup. ground=True puts lowest joint at z=0.'''
    warnings = []
    scale = float(scale)
    if preset not in (None, "mediapipe"):
        raise ValueError("preset must be None or 'mediapipe'")
    if preset == "mediapipe":
        raw = _mediapipe_skeleton(joints, min_visibility)
        P = {k: _to_vec(v, coords, scale) for k, v in raw.items()}
        P = {k: v for k, v in P.items() if v is not None}

        def g(*ks):
            vs = [P[k] for k in ks if k in P]
            return sum(vs, Vector()) / len(vs) if len(vs) == len(ks) else None

        hip_c, sh_c = g("left_hip", "right_hip"), g("left_shoulder", "right_shoulder")
        if hip_c is None or sh_c is None:
            raise ValueError("MediaPipe preset needs both hips and both shoulders (visible)")
        up = sh_c - hip_c
        head_c = g("left_ear", "right_ear") or P.get("nose") or (sh_c + up * 0.35)
        neck_base = sh_c + up * 0.05
        J, T, PAR = {}, {}, {}

        def add(k, head, tail, parent):
            if head is None or tail is None:
                warnings.append(f"skipped {k}: missing landmark")
                return
            J[k], T[k], PAR[k] = head, tail, parent

        add("hips", hip_c, hip_c.lerp(sh_c, 0.15), None)
        add("spine", hip_c.lerp(sh_c, 0.15), hip_c.lerp(sh_c, 0.5), "hips")
        add("chest", hip_c.lerp(sh_c, 0.5), neck_base, "spine")
        add("neck", neck_base, neck_base.lerp(head_c, 0.6), "chest")
        add("head", neck_base.lerp(head_c, 0.6), head_c + (head_c - neck_base) * 0.6, "neck")
        for s, side in (("left", "L"), ("right", "R")):
            sh, el, wr = P.get(f"{s}_shoulder"), P.get(f"{s}_elbow"), P.get(f"{s}_wrist")
            add(f"shoulder_{side}", neck_base.lerp(sh, 0.2) if sh else None, sh, "chest")
            add(f"upper_arm_{side}", sh, el, f"shoulder_{side}")
            add(f"forearm_{side}", el, wr, f"upper_arm_{side}")
            hand_tip = g(f"{s}_index", f"{s}_pinky")
            if hand_tip is None and wr is not None and el is not None:
                hand_tip = wr + (wr - el) * 0.35
            add(f"hand_{side}", wr, hand_tip, f"forearm_{side}")
            hp, kn, an = P.get(f"{s}_hip"), P.get(f"{s}_knee"), P.get(f"{s}_ankle")
            add(f"thigh_{side}", hp, kn, "hips")
            add(f"shin_{side}", kn, an, f"thigh_{side}")
            add(f"foot_{side}", an, P.get(f"{s}_foot_index"), f"shin_{side}")
        # re-parent to surviving ancestors handled below by the generic builder
        joints_v = {bone_name(k, naming): v for k, v in J.items()}
        tails_v = {bone_name(k, naming): v for k, v in T.items()}
        parents = {bone_name(k, naming): (bone_name(p, naming) if p else None) for k, p in PAR.items()}
    else:
        if isinstance(joints, (list, tuple)):  # list of {"name","position"/"pos","parent"}
            jd, pd = {}, {}
            for j in joints:
                if not isinstance(j, dict) or "name" not in j:
                    raise ValueError("list joints must be dicts with 'name', 'position' and optional 'parent'")
                jd[j["name"]] = j.get("position", j.get("pos"))
                pd[j["name"]] = j.get("parent")
            joints, parents = jd, (parents or pd)
        if not isinstance(joints, dict) or not joints:
            raise ValueError("joints must be a non-empty dict {name: [x,y,z]} (or preset='mediapipe' landmarks)")
        joints_v = {}
        for k, v in joints.items():
            vv = _to_vec(v, coords, scale)
            if vv is None:
                warnings.append(f"skipped {k}: invalid position {v!r}")
            else:
                joints_v[str(k)] = vv
        tails_v = {k: _to_vec(v, coords, scale) for k, v in (tails or {}).items()}
        tails_v = {k: v for k, v in tails_v.items() if v is not None}
        parents = dict(parents or {})
    if not joints_v:
        raise ValueError("no valid joints")
    # parent resolution: skip missing (walk up the original chain), unknown -> root, break cycles
    orig_parents = {k: parents.get(k) for k in parents}
    par = {}
    for k in joints_v:
        p = orig_parents.get(k)
        seen = {k}
        while p is not None and p not in joints_v:
            if p in seen:
                p = None
                break
            seen.add(p)
            nxt = orig_parents.get(p)
            if nxt is None and p not in orig_parents:
                warnings.append(f"{k}: parent {p!r} unknown -> root")
            p = nxt
        par[k] = p
    for k in list(par):  # cycle break
        seen, p = {k}, par[k]
        while p is not None:
            if p in seen:
                warnings.append(f"cycle at {k!r}: made root")
                par[k] = None
                break
            seen.add(p)
            p = par.get(p)
    children = {k: [c for c, p in par.items() if p == k] for k in joints_v}

    def subtree(k):
        return 1 + sum(subtree(c) for c in children[k])

    # tails
    tail = {}
    order = []
    todo = [k for k in joints_v if par[k] is None]
    while todo:
        k = todo.pop(0)
        order.append(k)
        todo.extend(children[k])
    for k in order:
        h = joints_v[k]
        t = tails_v.get(k)
        if t is None and children[k]:
            if len(children[k]) == 1:
                t = joints_v[children[k][0]]
            else:
                best = max(children[k], key=lambda c: (subtree(c), -(joints_v[c] - h).length))
                t = joints_v[best]
        if t is None:  # leaf: continue parent's direction
            p = par[k]
            if p is not None:
                d = (h - joints_v[p])
                t = h + (d.normalized() * max(d.length * 0.5, min_length) if d.length > 1e-9 else Vector((0, 0, min_length)))
            else:
                t = h + Vector((0, 0, max(min_length, 0.1 * scale)))
        if (t - h).length < min_length:
            p = par[k]
            d = (h - joints_v[p]) if p is not None else Vector((0, 0, 1))
            d = d.normalized() if d.length > 1e-9 else Vector((0, 0, 1))
            t = h + d * min_length
            warnings.append(f"{k}: zero-length bone nudged")
        tail[k] = t
    shift = Vector()
    if ground:
        zmin = min(min(v.z for v in joints_v.values()), min(v.z for v in tail.values()))
        shift = Vector((0, 0, -zmin))
    arm = bpy.data.armatures.new(name)
    obj = _link(bpy.data.objects.new(name, arm))
    obj.location = Vector(location)
    obj.show_in_front = True
    with _mode(obj, "EDIT"):
        ebs = {}
        for k in order:
            eb = arm.edit_bones.new(k)
            eb.head = joints_v[k] + shift
            eb.tail = tail[k] + shift
            ebs[k] = eb
        for k in order:
            if par[k] is not None:
                ebs[k].parent = ebs[par[k]]
                if connect and (ebs[par[k]].tail - ebs[k].head).length < 1e-4:
                    ebs[k].use_connect = True
        names = {k: eb.name for k, eb in ebs.items()}
    return {"armature": obj.name, "bones": len(arm.bones), "roots": [names[k] for k in order if par[k] is None],
            "hierarchy": {names[k]: (names[par[k]] if par[k] else None) for k in order}, "warnings": warnings[:20]}


@recipe("rigging", tags=("skinning", "weights", "parent", "automatic weights", "bind"))
def auto_weight_parent(mesh=None, armature=None, method="auto", fill_unweighted=True) -> dict:
    '''Parent a mesh to an armature with automatic (bone-heat) weights; falls back to envelope then to nearest-bone
    weights for any vertex left unweighted. method: auto | envelope | nearest. Returns unweighted-vertex count.
    mesh/armature None -> builds a demo low-poly character + fitted biped.'''
    if mesh is None:
        m = _demo_character()
        mesh = m.name
        if armature is None:
            armature = biped_armature(name="DemoRig", fit_to=mesh)["armature"]
    me_obj = _mesh(mesh)
    if armature is None:
        raise ValueError("armature is required when mesh is given")
    arm_obj = _armature(armature)
    if method not in ("auto", "envelope", "nearest"):
        raise ValueError("method must be auto, envelope or nearest")
    _ensure_object_mode()
    mw = me_obj.matrix_world.copy()
    used = method
    ok = False
    if method in ("auto", "envelope"):
        for typ in (["ARMATURE_AUTO", "ARMATURE_ENVELOPE"] if method == "auto" else ["ARMATURE_ENVELOPE"]):
            try:
                ov = dict(find_view3d())
                ov.update(active_object=arm_obj, object=arm_obj, selected_objects=[me_obj, arm_obj],
                          selected_editable_objects=[me_obj, arm_obj])
                with bpy.context.temp_override(**ov):
                    res = bpy.ops.object.parent_set(type=typ, keep_transform=True)
                if "FINISHED" in res:
                    used = typ.split("_")[1].lower()
                    ok = True
                    break
            except Exception:
                continue
    if not ok:  # manual parent + modifier
        me_obj.parent = arm_obj
        me_obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
        me_obj.matrix_world = mw
        used = "nearest"
    if not any(md.type == "ARMATURE" and md.object == arm_obj for md in me_obj.modifiers):
        md = me_obj.modifiers.new("Armature", "ARMATURE")
        md.object = arm_obj
    # unweighted check + fill
    deform = _deform_bones(arm_obj)
    vg_names = {b.name for b in deform}
    idx2name = {g.index: g.name for g in me_obj.vertex_groups}

    def unweighted():
        return [v for v in me_obj.data.vertices
                if not any(idx2name.get(g.group) in vg_names and g.weight > 1e-6 for g in v.groups)]

    missing = unweighted()
    n_missing_before = len(missing)
    if missing and fill_unweighted:
        to_arm = arm_obj.matrix_world.inverted() @ me_obj.matrix_world
        segs = [(b.name, b.head_local, b.tail_local) for b in deform]
        for b in deform:
            if b.name not in me_obj.vertex_groups:
                me_obj.vertex_groups.new(name=b.name)
        for v in missing:
            p = to_arm @ v.co
            best = min(segs, key=lambda s: _seg_dist(p, s[1], s[2]))
            me_obj.vertex_groups[best[0]].add([v.index], 1.0, "REPLACE")
    left = len(unweighted())
    return {"mesh": me_obj.name, "armature": arm_obj.name, "method": used, "vertex_groups": len(me_obj.vertex_groups),
            "unweighted_before_fill": n_missing_before, "unweighted": left, "parent": me_obj.parent.name if me_obj.parent else None}


@recipe("rigging", tags=("weights", "unity", "gltf", "normalize", "influences", "export"))
def limit_weights_and_normalize(mesh=None, limit=4, prune_below=0.01, only_deform_armature=None) -> dict:
    '''Keep the strongest `limit` bone influences per vertex (Unity/glTF = 4), prune weights < prune_below, normalize to 1.
    only_deform_armature=name restricts to groups that match that armature's deform bones. mesh None -> demo mesh with 6 overlapping groups.'''
    if mesh is None:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
        o = bpy.context.active_object
        o.name = "WeightDemo"
        for i in range(6):
            g = o.vertex_groups.new(name=f"Bone{i}")
            for v in o.data.vertices:
                g.add([v.index], ((v.index * (i + 3)) % 7 + 1) / 8.0, "REPLACE")
        mesh = o.name
    obj = _mesh(mesh)
    if limit < 1:
        raise ValueError("limit must be >= 1")
    allowed = None
    if only_deform_armature:
        allowed = {b.name for b in _deform_bones(_armature(only_deform_armature))}
    gidx = {g.index: g for g in obj.vertex_groups if allowed is None or g.name in allowed}
    max_before = removed = 0
    for v in obj.data.vertices:
        ws = [(g.group, g.weight) for g in v.groups if g.group in gidx]
        max_before = max(max_before, len(ws))
        ws.sort(key=lambda t: -t[1])
        keep = [(gi, w) for gi, w in ws[:limit] if w >= prune_below] or ws[:1]
        drop = [gi for gi, _ in ws if gi not in {k for k, _ in keep}]
        for gi in drop:
            gidx[gi].remove([v.index])
            removed += 1
        tot = sum(w for _, w in keep)
        if tot > 0:
            for gi, w in keep:
                gidx[gi].add([v.index], w / tot, "REPLACE")
    max_after = max((sum(1 for g in v.groups if g.group in gidx) for v in obj.data.vertices), default=0)
    sums = [sum(g.weight for g in v.groups if g.group in gidx) for v in obj.data.vertices]
    sums = [s for s in sums if s > 0]
    return {"mesh": obj.name, "max_influences_before": max_before, "max_influences_after": max_after,
            "weights_removed": removed, "sum_min": r(min(sums)) if sums else None, "sum_max": r(max(sums)) if sums else None}


@recipe("rigging", tags=("ik", "inverse kinematics", "pole", "arm", "leg", "constraint", "foot"))
def ik_chain(armature=None, bone="shin_L", chain_length=2, pole=True, pole_distance=None, control_name=None,
             orient_child=True) -> dict:
    '''Add an IK control bone + pole target for a limb. bone = the LAST bone of the chain (shin/forearm; canonical keys ok).
    Creates IK_<bone> at the bone tail (unparented, non-deform) and Pole_<bone> in front of the middle joint, computes
    pole_angle so the rest pose does not twist. orient_child: child (foot/hand) copies the control's rotation (basic foot roll).
    armature None -> demo biped.'''
    if armature is None:
        armature = _demo_rig()
    obj = _armature(armature)
    bname = resolve_bone(obj, bone)
    ctrl = control_name or f"IK_{bname.replace(':', '_')}"
    pole_name = f"Pole_{bname.replace(':', '_')}"
    child_name = None
    with _mode(obj, "EDIT") as arm:
        eb = arm.edit_bones[bname]
        chain = [eb]
        for _ in range(chain_length - 1):
            if chain[-1].parent is None:
                break
            chain.append(chain[-1].parent)
        base = chain[-1]
        L = sum(b.length for b in chain)
        c = arm.edit_bones.get(ctrl) or arm.edit_bones.new(ctrl)
        c.head = eb.tail.copy()
        c.tail = eb.tail + (eb.tail - eb.head).normalized() * eb.length * 0.3
        kids = [k for k in eb.children]
        if kids:
            child_name = kids[0].name
            c.tail = c.head + (kids[0].tail - kids[0].head)
            c.roll = kids[0].roll
        c.use_deform = False
        c.parent = None
        pole_info = None
        if pole:
            mid = base.tail
            start, end = base.head, eb.tail
            axis = end - start
            proj = start + axis * ((mid - start).dot(axis) / max(axis.length_squared, 1e-12))
            bend = mid - proj
            if bend.length < 1e-5:  # straight chain: guess forward for legs (-Y), back for arms (+Y)
                bend = Vector((0, -1, 0)) if abs(axis.z) > abs(axis.x) else Vector((0, 1, 0))
            dist = pole_distance or L * 0.8
            pl = mid + bend.normalized() * dist
            p = arm.edit_bones.get(pole_name) or arm.edit_bones.new(pole_name)
            p.head = pl
            p.tail = pl + Vector((0, 0, L * 0.1))
            p.use_deform = False
            p.parent = None
            # pole angle (rest pose)
            pn = (eb.tail - base.head).cross(pl - base.head)
            ppa = pn.cross(base.tail - base.head)
            ang = base.x_axis.angle(ppa)
            if base.x_axis.cross(ppa).angle(base.tail - base.head) < 1:
                ang = -ang
            pole_info = ang
    pb = obj.pose.bones[bname]
    for cn in [c for c in pb.constraints if c.type == "IK"]:
        pb.constraints.remove(cn)
    ik = pb.constraints.new("IK")
    ik.target = obj
    ik.subtarget = ctrl
    ik.chain_count = chain_length
    if pole:
        ik.pole_target = obj
        ik.pole_subtarget = pole_name
        ik.pole_angle = pole_info
    if orient_child and child_name:
        cr = obj.pose.bones[child_name].constraints.new("COPY_ROTATION")
        cr.target = obj
        cr.subtarget = ctrl
    return {"armature": obj.name, "bone": bname, "control": ctrl, "pole": pole_name if pole else None,
            "pole_angle_deg": r(math.degrees(pole_info), 1) if pole else None, "chain_length": chain_length,
            "child_follows": child_name if orient_child else None}


@recipe("rigging", tags=("constraint", "look at", "track", "aim", "head", "eyes", "camera"))
def look_at_constraint(owner=None, bone=None, target=None, target_location=(0, -2, 1.6), influence=1.0, damped=True) -> dict:
    '''Make an object or a bone aim at a target (Damped Track for bones / Track To for objects like cameras).
    owner+bone e.g. ('Rig','head'); target=None creates an Empty 'LookTarget' at target_location you can animate.
    owner None -> demo biped + head bone. Bones aim their Y axis; objects aim -Z with Y up (camera convention).'''
    if owner is None:
        owner, bone = _demo_rig(), "head"
    obj = get_obj(owner)
    if target is None:
        tgt = _link(bpy.data.objects.new("LookTarget", None))
        tgt.empty_display_type = "SPHERE"
        tgt.empty_display_size = 0.1
        tgt.location = Vector(target_location)
    else:
        tgt = get_obj(target)
    if bone:
        if obj.type != "ARMATURE":
            raise ValueError("bone given but owner is not an armature")
        bname = resolve_bone(obj, bone)
        holder = obj.pose.bones[bname]
        c = holder.constraints.new("DAMPED_TRACK")
        c.track_axis = "TRACK_Y"
    else:
        bname = None
        holder = obj
        if damped:
            c = holder.constraints.new("DAMPED_TRACK")
            c.track_axis = "TRACK_NEGATIVE_Z" if obj.type in ("CAMERA", "LIGHT") else "TRACK_Y"
        else:
            c = holder.constraints.new("TRACK_TO")
            c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    c.target = tgt
    c.influence = influence
    return {"owner": obj.name, "bone": bname, "target": tgt.name, "constraint": c.name, "type": c.type}


@recipe("rigging", tags=("child of", "prop", "pickup", "hand", "grab", "constraint", "training"))
def child_of_pickup(prop=None, armature=None, bone="hand_R", frame_pick=10, frame_release=None) -> dict:
    '''Animate a prop being picked up by a bone (hand) and optionally released: Child Of constraint with keyed influence
    (0->1 at frame_pick, inverse set so nothing jumps) and, on release, the prop keeps its world pose (keys its transform).
    Typical for XR training (tools, parts). prop/armature None -> demo cube + biped.'''
    if armature is None:
        armature = _demo_rig()
    arm = _armature(armature)
    bname = resolve_bone(arm, bone)
    if prop is None:
        bpy.ops.mesh.primitive_cube_add(size=0.08)
        po = bpy.context.active_object
        po.name = "Prop"
        po.location = arm.matrix_world @ arm.data.bones[bname].tail_local
        prop = po.name
    po = get_obj(prop)
    sc = bpy.context.scene
    c = po.constraints.new("CHILD_OF")
    c.name = "PickUp"
    c.target, c.subtarget = arm, bname
    sc.frame_set(frame_pick)
    pm = arm.matrix_world @ arm.pose.bones[bname].matrix
    c.inverse_matrix = pm.inverted()
    c.influence = 0.0
    c.keyframe_insert("influence", frame=frame_pick - 1)
    c.influence = 1.0
    c.keyframe_insert("influence", frame=frame_pick)
    po.keyframe_insert("location", frame=frame_pick)
    po.keyframe_insert("rotation_euler", frame=frame_pick)
    keys = 2
    if frame_release:
        if frame_release <= frame_pick:
            raise ValueError("frame_release must be after frame_pick")
        sc.frame_set(frame_release)
        bpy.context.view_layer.update()
        world = po.matrix_world.copy()
        c.keyframe_insert("influence", frame=frame_release - 1)
        po.keyframe_insert("location", frame=frame_release - 1)
        po.keyframe_insert("rotation_euler", frame=frame_release - 1)
        c.influence = 0.0
        c.keyframe_insert("influence", frame=frame_release)
        po.matrix_world = world if po.parent is None else po.matrix_world
        po.location = world.to_translation()
        po.rotation_euler = world.to_euler()
        po.keyframe_insert("location", frame=frame_release)
        po.keyframe_insert("rotation_euler", frame=frame_release)
        keys = 4
        for fc in _fcurves(po):
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"
    for fc in _fcurves(po):
        if "influence" in fc.data_path:
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"
    sc.frame_set(1)
    return {"prop": po.name, "armature": arm.name, "bone": bname, "constraint": c.name,
            "frame_pick": frame_pick, "frame_release": frame_release, "influence_keys": keys}


def _auto_group(name):
    c = canon_of(name)
    n = name.lower()
    if n.startswith(("ik_", "pole_", "ctrl", "mch", "root")) or n == "root":
        return "Controls"
    if c is None:
        return "Other"
    side = "L" if c.endswith("_L") else "R" if c.endswith("_R") else ""
    if any(k in c for k in ("shoulder", "arm", "hand")):
        return f"Arm.{side}"
    if any(k in c for k in ("thigh", "shin", "foot", "toe")):
        return f"Leg.{side}"
    return "Torso"


_GROUP_COLORS = {"Torso": "THEME09", "Arm.L": "THEME03", "Arm.R": "THEME01", "Leg.L": "THEME04", "Leg.R": "THEME02",
                 "Controls": "THEME05", "Other": "THEME10"}


@recipe("rigging", tags=("bone collections", "organize", "layers", "colors", "5.x"))
def organize_bone_collections(armature=None, groups=None, color=True, clear_existing=True, hide=()) -> dict:
    '''Sort bones into Bone Collections (Blender 4+/5 API, replaces layers) and colour them.
    groups=None auto-groups by body part: Torso, Arm.L/R, Leg.L/R, Controls (IK_/Pole_/Root), Other.
    groups={"Face": ["jaw*", "eye*"], ...} uses fnmatch patterns. hide=["Controls"] hides those collections.'''
    import fnmatch
    if armature is None:
        armature = _demo_rig()
        ik_chain(armature, "shin_L")
    obj = _armature(armature)
    arm = obj.data
    if clear_existing:
        for bc in list(arm.collections_all):
            arm.collections.remove(bc)
    assign = {}
    for b in arm.bones:
        g = None
        if groups:
            for gname, pats in groups.items():
                if any(fnmatch.fnmatch(b.name, p) or fnmatch.fnmatch(b.name.lower(), p.lower()) for p in pats):
                    g = gname
                    break
            g = g or "Other"
        else:
            g = _auto_group(b.name)
        assign.setdefault(g, []).append(b.name)
    out = {}
    for g, names in assign.items():
        bc = arm.collections.get(g) or arm.collections.new(g)
        for n in names:
            bc.assign(arm.bones[n])
            if color:
                arm.bones[n].color.palette = _GROUP_COLORS.get(g, "THEME10")
        bc.is_visible = g not in hide
        out[g] = len(names)
    return {"armature": obj.name, "collections": out}


@recipe("rigging", tags=("rename", "mixamo", "unity", "naming", "convention", "retarget"))
def rename_bones_to_convention(armature=None, to="unity", strip_prefix=True) -> dict:
    '''Rename humanoid bones between conventions: 'blender' (UpperArm.L), 'mixamo' (mixamorig:LeftArm), 'unity'
    (LeftUpperArm). Source convention is auto-detected per bone; vertex groups and F-curves follow automatically.
    Unrecognised bones are kept and listed. armature None -> demo biped (blender names).'''
    if to not in CONVENTIONS:
        raise ValueError(f"to must be one of {CONVENTIONS}")
    if armature is None:
        armature = _demo_rig()
    obj = _armature(armature)
    before = detect_naming(obj)
    renamed, unknown = 0, []
    for b in list(obj.data.bones):
        c = canon_of(b.name)
        if c is None:
            unknown.append(b.name)
            continue
        new = bone_name(c, to)
        if b.name != new:
            old = b.name
            b.name = new
            renamed += 1
            for ch in obj.children:  # safety: older versions don't rename groups on children
                if ch.type == "MESH" and old in ch.vertex_groups and new not in ch.vertex_groups:
                    ch.vertex_groups[old].name = new
    return {"armature": obj.name, "from": before, "to": to, "renamed": renamed, "unrecognised": unknown[:30],
            "sample": [b.name for b in obj.data.bones][:6]}


@recipe("rigging", tags=("rest pose", "apply pose", "a-pose", "t-pose", "bind pose"))
def apply_pose_as_rest(armature=None, apply_to_meshes=True) -> dict:
    '''Make the current pose the new rest pose (e.g. convert A-pose to T-pose for Unity). Skinned meshes are first
    deformed into the pose (armature modifier applied & re-added) so nothing moves. Shape-keyed meshes are skipped
    (reported). armature None -> demo biped whose arms are posed down 40 degrees.'''
    if armature is None:
        armature = _demo_rig()
        o = bpy.data.objects[armature]
        for s, sg in (("L", 1), ("R", -1)):
            pb = o.pose.bones[resolve_bone(o, f"upper_arm_{s}")]
            pb.rotation_mode = "QUATERNION"
            pb.rotation_quaternion = _rot_local(pb, (0, 1, 0), sg * math.radians(40))
    obj = _armature(armature)
    bpy.context.view_layer.update()
    meshes, skipped = [], []
    if apply_to_meshes:
        dg = bpy.context.evaluated_depsgraph_get()
        for m in bpy.data.objects:
            if m.type != "MESH" or not any(md.type == "ARMATURE" and md.object == obj for md in m.modifiers):
                continue
            if m.data.shape_keys:
                skipped.append(m.name)
                continue
            new = bpy.data.meshes.new_from_object(m.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
            # only the armature should be baked: other modifiers stay live -> temporarily disable them
            old = m.data
            m.data = new
            meshes.append(m.name)
            if old.users == 0:
                bpy.data.meshes.remove(old)
    pose_mats = {pb.name: pb.matrix.copy() for pb in obj.pose.bones}
    with _mode(obj, "EDIT") as arm:
        connected = {eb.name: eb.use_connect for eb in arm.edit_bones}
        for eb in arm.edit_bones:
            eb.use_connect = False
        for eb in arm.edit_bones:  # matrices are armature-space, order does not matter once disconnected
            L = eb.length
            eb.matrix = pose_mats[eb.name]
            eb.length = L
        for eb in arm.edit_bones:
            if connected[eb.name] and eb.parent and (eb.parent.tail - eb.head).length < 1e-4:
                eb.use_connect = True
    for pb in obj.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.scale = (1, 1, 1)
    return {"armature": obj.name, "meshes_rebound": meshes, "skipped_shape_key_meshes": skipped,
            "note": "non-armature modifiers on rebound meshes were baked too" if meshes else ""}


@recipe("rigging", tags=("scale", "apply scale", "fbx", "unity", "export", "centimeter"))
def scale_rig_fix(armature=None, include_children=True, scale_animation=True) -> dict:
    '''Apply rotation+scale on an armature (and its child meshes) so everything is at scale 1 without moving — fixes
    100x/0.01 FBX rigs before Unity export. Pose-bone location keys are scaled too (uniform scale only).
    armature None -> demo biped at scale 0.01 with a parented cube.'''
    if armature is None:
        armature = biped_armature(name="DemoRig", height=180.0)["armature"]
        o = bpy.data.objects[armature]
        o.scale = (0.01, 0.01, 0.01)
        bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0, 0, 0.9))
        cube = bpy.context.active_object
        bpy.context.view_layer.update()
        cube.parent = o
        cube.matrix_parent_inverse = o.matrix_world.inverted()
    obj = _armature(armature)
    bpy.context.view_layer.update()
    mb = obj.matrix_basis.copy()
    loc, rot, sca = mb.decompose()
    RS = (rot.to_matrix() @ Matrix.Diagonal(sca)).to_4x4()
    kids = [c for c in obj.children if include_children]
    kid_world = {c.name: c.matrix_world.copy() for c in kids}
    obj.data.transform(RS)
    obj.matrix_basis = Matrix.Translation(loc)
    s_uniform = abs(sca.x - sca.y) < 1e-6 and abs(sca.y - sca.z) < 1e-6
    scaled_curves = 0
    if scale_animation and s_uniform and abs(sca.x - 1) > 1e-6:
        for fc in _fcurves(obj):
            if fc.data_path.endswith(".location"):
                for kp in fc.keyframe_points:
                    kp.co.y *= sca.x
                    kp.handle_left.y *= sca.x
                    kp.handle_right.y *= sca.x
                scaled_curves += 1
        for pb in obj.pose.bones:
            pb.location = pb.location * sca.x
    bpy.context.view_layer.update()
    fixed = []
    for c in kids:
        W = kid_world[c.name]
        new_parent_world = obj.matrix_world
        local = new_parent_world.inverted() @ W
        if c.type == "MESH" and c.data.users == 1:
            c.data.transform(local)
            c.matrix_parent_inverse = Matrix()
            c.matrix_basis = Matrix()
        else:
            c.matrix_parent_inverse = Matrix()
            c.matrix_basis = local
        fixed.append(c.name)
    bpy.context.view_layer.update()
    return {"armature": obj.name, "applied_scale": r(list(sca)), "applied_rotation_deg": r([math.degrees(a) for a in rot.to_euler()], 1),
            "children_fixed": fixed, "location_fcurves_scaled": scaled_curves,
            "rig_height": r(max(b.tail_local.z for b in obj.data.bones))}


@recipe("rigging", tags=("root bone", "root motion", "unity", "hierarchy"))
def add_root_bone(armature=None, name="Root", length=None) -> dict:
    '''Add a root bone at the armature origin (pointing +Y, on the floor) and parent all current top-level bones to it.
    Needed for root motion in Unity/Unreal. No-op if the bone exists. armature None -> demo biped.'''
    if armature is None:
        armature = _demo_rig()
    obj = _armature(armature)
    if name in obj.data.bones:
        return {"armature": obj.name, "root": name, "reparented": [], "note": "already exists"}
    tops = [b.name for b in obj.data.bones if b.parent is None]
    height = max((b.tail_local.z for b in obj.data.bones), default=1.0)
    with _mode(obj, "EDIT") as arm:
        rb = arm.edit_bones.new(name)
        rb.head = (0, 0, 0)
        rb.tail = (0, length or max(height * 0.15, 0.01), 0)
        rb.use_deform = False
        for t in tops:
            arm.edit_bones[t].parent = rb
            arm.edit_bones[t].use_connect = False
    return {"armature": obj.name, "root": name, "reparented": tops}


# --------------------------------------------------------------------------------------------- animation
@recipe("rigging", tags=("keyframe", "animation", "transform", "keys"))
def keyframe_transform(target=None, keys=None, bone=None, interpolation="BEZIER") -> dict:
    '''Insert transform keyframes in one call. keys=[{frame, location?, rotation_deg?, scale?}, ...]; rotation is Euler
    degrees (pose bones are switched to XYZ Euler). bone keys a pose bone of an armature target.
    target None -> demo cube with a 3-key move/rotate.'''
    if target is None:
        bpy.ops.mesh.primitive_cube_add()
        target = bpy.context.active_object.name
    if keys is None:
        keys = [{"frame": 1, "location": (0, 0, 0)}, {"frame": 12, "location": (2, 0, 0), "rotation_deg": (0, 0, 90)},
                {"frame": 24, "location": (2, 2, 0), "rotation_deg": (0, 0, 180), "scale": (1, 1, 1)}]
    if not keys:
        raise ValueError("keys must be a non-empty list of {frame, location/rotation_deg/scale}")
    obj = get_obj(target)
    holder = obj
    if bone:
        holder = obj.pose.bones[resolve_bone(obj, bone)]
    holder.rotation_mode = "XYZ"
    grp = holder.name if bone else None
    n = 0
    for k in keys:
        if "frame" not in k:
            raise ValueError(f"key without 'frame': {k}")
        f = k["frame"]
        for prop, val in (("location", k.get("location")), ("rotation_euler", k.get("rotation_deg")), ("scale", k.get("scale"))):
            if val is None:
                continue
            if prop == "rotation_euler":
                val = [math.radians(a) for a in val]
            setattr(holder, prop, val)
            if grp:
                holder.keyframe_insert(prop, frame=f, group=grp)
            else:
                holder.keyframe_insert(prop, frame=f)
            n += 1
    for fc in _fcurves(obj):
        if bone and _bone_of_path(fc.data_path) != holder.name:
            continue
        for kp in fc.keyframe_points:
            kp.interpolation = interpolation
    act = obj.animation_data.action
    return {"target": obj.name, "bone": holder.name if bone else None, "keyed_channels": n,
            "action": act.name, "frame_range": r(list(act.frame_range))}


def _sample_frames(cycle, samples):
    return [1 + cycle * i / samples for i in range(samples + 1)]


@recipe("rigging", tags=("walk cycle", "procedural", "locomotion", "animation", "loop", "biped"))
def procedural_walk_cycle(armature=None, cycle_frames=24, stride_deg=28.0, knee_deg=45.0, arm_swing_deg=20.0,
                          arms_down_deg=65.0, bob=0.03, action_name="Walk", in_place=True, speed=1.4) -> dict:
    '''Keyframe a looping in-place walk on a humanoid rig (any naming convention): thighs/shins/feet, arm swing,
    hips bob + sway, cyclic F-modifiers. arms_down_deg lowers T-pose arms. in_place=False also keys forward travel of
    the armature object at `speed` m/s (no cycles on that). armature None -> demo biped.'''
    if armature is None:
        armature = _demo_rig()
    obj = _armature(armature)
    cm = canon_map(obj)
    need = ["hips", "thigh_L", "thigh_R", "shin_L", "shin_R"]
    miss = [k for k in need if k not in cm]
    if miss:
        raise ValueError(f"armature lacks humanoid bones {miss}; rename_bones_to_convention or check names")
    act = _new_action(obj, action_name)
    P = obj.pose.bones
    fps = bpy.context.scene.render.fps
    samples = 8
    H = max(b.tail_local.z for b in obj.data.bones)
    for i, f in enumerate(_sample_frames(cycle_frames, samples)):
        ph = 2 * math.pi * i / samples
        for side, sg in (("L", 1), ("R", -1)):
            s = math.sin(ph) * sg            # +1 = leg forward
            c = math.cos(ph) * sg
            _key_q(P[cm[f"thigh_{side}"]], _rot_local(P[cm[f"thigh_{side}"]], (1, 0, 0), -math.radians(stride_deg) * s), f)
            knee = math.radians(knee_deg) * max(0.0, c) + math.radians(5)
            _key_q(P[cm[f"shin_{side}"]], _rot_local(P[cm[f"shin_{side}"]], (1, 0, 0), knee), f)
            if f"foot_{side}" in cm:
                _key_q(P[cm[f"foot_{side}"]], _rot_local(P[cm[f"foot_{side}"]], (1, 0, 0), -math.radians(10) * max(0.0, -s)), f)
            if f"upper_arm_{side}" in cm:
                pb = P[cm[f"upper_arm_{side}"]]
                down = Quaternion((0, 1, 0), sg * math.radians(arms_down_deg))
                swing = Quaternion((1, 0, 0), math.radians(arm_swing_deg) * s)  # opposite leg: +s = arm back
                _key_q(pb, _q_world(pb, swing @ down), f)
            if f"forearm_{side}" in cm:
                pb = P[cm[f"forearm_{side}"]]
                _key_q(pb, _rot_local(pb, (0, 0, 1), -sg * math.radians(15 + 10 * max(0.0, -s))), f)
        hp = P[cm["hips"]]
        hp.location = _vec_local(hp, (math.sin(ph) * bob * 0.5 * H / 1.8, 0, -abs(math.cos(ph)) * bob * H / 1.8))
        hp.keyframe_insert("location", frame=f, group=hp.name)
        _key_q(hp, _rot_local(hp, (0, 0, 1), math.radians(5) * math.sin(ph)), f)
        for k, amt in (("spine", -3), ("chest", -3)):
            if k in cm:
                _key_q(P[cm[k]], _rot_local(P[cm[k]], (0, 0, 1), math.radians(amt) * math.sin(ph)), f)
    curves = _fcurves(obj)
    for fc in curves:
        for kp in fc.keyframe_points:
            kp.interpolation = "BEZIER"
        fc.update()
    ncyc = _cyclic(curves)
    if not in_place:
        dist = speed * cycle_frames / fps
        obj.location.y = 0
        obj.keyframe_insert("location", index=1, frame=1)
        obj.location.y = -dist
        obj.keyframe_insert("location", index=1, frame=1 + cycle_frames)
        for fc in _fcurves(obj):
            if fc.data_path == "location":
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
                fc.extrapolation = "LINEAR"
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, cycle_frames
    return {"armature": obj.name, "action": act.name, "fcurves": len(_fcurves(obj)), "cyclic_modifiers": ncyc,
            "frame_range": [1, 1 + cycle_frames], "loop_frames": cycle_frames,
            "tip": "scene range set to 1..cycle_frames (last key == first) for seamless loop export"}


@recipe("rigging", tags=("idle", "breathing", "loop", "animation", "procedural"))
def idle_breathing(target=None, cycle_frames=96, amount=1.0, action_name="Idle") -> dict:
    '''Subtle looping idle: chest/upper chest breathe (rotation + slight scale), shoulders rise, head drifts, hips sway.
    Works on humanoid armatures (any naming); for a plain object it keys a gentle scale pulse. amount scales intensity.
    target None -> demo biped.'''
    if target is None:
        target = _demo_rig()
    obj = get_obj(target)
    act = _new_action(obj, action_name)
    samples = 8
    frames = _sample_frames(cycle_frames, samples)
    if obj.type == "ARMATURE":
        cm = canon_map(obj)
        P = obj.pose.bones
        keyed = []
        for i, f in enumerate(frames):
            ph = 2 * math.pi * i / samples
            br = math.sin(ph) * amount
            for k, ax, deg in (("chest", (1, 0, 0), 1.5), ("upper_chest", (1, 0, 0), 2.0), ("spine", (1, 0, 0), 0.8),
                               ("neck", (1, 0, 0), -1.5), ("head", (0, 0, 1), 0.0)):
                if k not in cm:
                    continue
                pb = P[cm[k]]
                q = _rot_local(pb, ax, math.radians(deg) * br)
                if k == "head":
                    q = _rot_local(pb, (0, 0, 1), math.radians(2.0 * amount) * math.sin(ph * 1 + 1.0))
                _key_q(pb, q, f)
                if k in ("chest", "upper_chest"):
                    pb.scale = (1 + 0.01 * br, 1, 1 + 0.015 * br)
                    pb.keyframe_insert("scale", frame=f, group=pb.name)
                keyed.append(k)
            for side, sg in (("L", 1), ("R", -1)):
                if f"shoulder_{side}" in cm:
                    pb = P[cm[f"shoulder_{side}"]]
                    _key_q(pb, _rot_local(pb, (0, 1, 0), -sg * math.radians(1.5) * br), f)
            if "hips" in cm:
                pb = P[cm["hips"]]
                _key_q(pb, _rot_local(pb, (0, 0, 1), math.radians(1.0 * amount) * math.sin(ph)), f)
        chans = sorted(set(keyed))
    else:
        for i, f in enumerate(frames):
            br = math.sin(2 * math.pi * i / samples) * amount
            obj.scale = (1 + 0.01 * br, 1 + 0.01 * br, 1 + 0.02 * br)
            obj.keyframe_insert("scale", frame=f)
        chans = ["scale"]
    curves = _fcurves(obj)
    _cyclic(curves)
    return {"target": obj.name, "action": act.name, "fcurves": len(curves), "channels": chans,
            "frame_range": [1, 1 + cycle_frames]}


@recipe("rigging", tags=("follow path", "curve", "path", "npc", "camera", "banking", "constraint"))
def follow_path(target=None, points=None, frames=120, cyclic=True, follow=True, bank_deg=0.0, curve_name="Path",
                forward_axis="FORWARD_Y", start_frame=1) -> dict:
    '''Move an object along a path: builds a curve from points (default: 4 m circle), adds Follow Path constraint with
    keyed offset (linear, 0->1 over `frames`). follow aligns to tangent (forward_axis e.g. FORWARD_Y / TRACK_NEGATIVE_Y);
    bank_deg tilts into turns. Object location is zeroed (constraint drives it). target None -> demo cone.'''
    if points is None:
        points = [(4 * math.cos(a), 4 * math.sin(a), 0) for a in [i * math.pi / 4 for i in range(8)]]
    if len(points) < 2:
        raise ValueError("points needs at least 2 [x,y,z] entries")
    if target is None:
        bpy.ops.mesh.primitive_cone_add(radius1=0.3, depth=0.8, rotation=(math.radians(-90), 0, 0))
        target = bpy.context.active_object.name
    obj = get_obj(target)
    cu = bpy.data.curves.new(curve_name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("NURBS")
    sp.points.add(len(points) - 1)
    pts = [Vector(p) for p in points]
    n = len(pts)
    for i, p in enumerate(pts):
        sp.points[i].co = (p.x, p.y, p.z, 1.0)
        if bank_deg and n >= 3 and (cyclic or 0 < i < n - 1):
            a, b = pts[(i - 1) % n], pts[(i + 1) % n]
            turn = (p - a).normalized().cross((b - p).normalized()).z  # >0 = left turn
            sp.points[i].tilt = math.radians(bank_deg) * max(-1.0, min(1.0, turn * 2)) * -1
    sp.use_cyclic_u = cyclic
    sp.use_endpoint_u = not cyclic
    sp.order_u = min(4, n)
    cu.use_path = True
    cu.path_duration = frames
    cu_obj = _link(bpy.data.objects.new(curve_name, cu))
    obj.location = (0, 0, 0)
    c = obj.constraints.new("FOLLOW_PATH")
    c.target = cu_obj
    c.use_curve_follow = follow
    c.forward_axis = forward_axis
    c.up_axis = "UP_Z"
    c.use_fixed_location = True
    c.offset_factor = 0.0
    c.keyframe_insert("offset_factor", frame=start_frame)
    c.offset_factor = 1.0
    c.keyframe_insert("offset_factor", frame=start_frame + frames)
    for fc in _fcurves(obj):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    return {"target": obj.name, "curve": cu_obj.name, "points": n, "constraint": c.name,
            "frame_range": [start_frame, start_frame + frames], "banked": bool(bank_deg)}


@recipe("rigging", tags=("bake", "ik", "constraints", "visual keying", "export", "fbx", "unity"))
def bake_action(target=None, frame_start=None, frame_end=None, step=1, remove_constraints=True, action_name=None,
                only_deform=False) -> dict:
    '''Bake constraint/IK/driver-driven motion into plain keyframes (visual transforms) on a new action — required before
    FBX/glTF export to Unity. Armatures: every pose bone (or only deform bones); objects: object transform.
    remove_constraints deletes the now-redundant constraints. target None -> demo biped with animated leg IK.'''
    sc = bpy.context.scene
    if target is None:
        target = _demo_rig()
        info = ik_chain(target, "shin_L", orient_child=False)
        ob = bpy.data.objects[target]
        ctrl = ob.pose.bones[info["control"]]
        for f, z in ((1, 0.0), (12, 0.25), (24, 0.0)):
            ctrl.location = (0, 0, 0)
            ctrl.location = _vec_local(ctrl, (0, 0, z))
            ctrl.keyframe_insert("location", frame=f)
        frame_start, frame_end = 1, 24
    obj = get_obj(target)
    fs = frame_start if frame_start is not None else sc.frame_start
    fe = frame_end if frame_end is not None else sc.frame_end
    if fe < fs:
        raise ValueError("frame_end < frame_start")
    frames = list(range(fs, fe + 1, max(1, step)))
    if frames[-1] != fe:
        frames.append(fe)
    data = {}
    if obj.type == "ARMATURE":
        bones = [pb for pb in obj.pose.bones if (pb.bone.use_deform or not only_deform)]
        for f in frames:
            sc.frame_set(f)
            data[f] = {pb.name: obj.convert_space(pose_bone=pb, matrix=pb.matrix, from_space="POSE", to_space="LOCAL")
                       for pb in bones}
    else:
        for f in frames:
            sc.frame_set(f)
            data[f] = obj.matrix_world.copy()
    removed = 0
    if remove_constraints:
        if obj.type == "ARMATURE":
            for pb in obj.pose.bones:
                for c in list(pb.constraints):
                    pb.constraints.remove(c)
                    removed += 1
        for c in list(obj.constraints):
            obj.constraints.remove(c)
            removed += 1
    old = obj.animation_data.action if obj.animation_data else None
    act = _new_action(obj, action_name or ((old.name if old else obj.name) + "_baked"))
    if obj.type == "ARMATURE":
        for f in frames:
            for name, M in data[f].items():
                pb = obj.pose.bones[name]
                pb.rotation_mode = "QUATERNION"
                pb.matrix_basis = M
                pb.keyframe_insert("location", frame=f, group=name)
                pb.keyframe_insert("rotation_quaternion", frame=f, group=name)
                pb.keyframe_insert("scale", frame=f, group=name)
    else:
        for f in frames:
            M = data[f]
            if obj.parent:
                M = (obj.parent.matrix_world @ obj.matrix_parent_inverse).inverted() @ M
            obj.matrix_basis = M
            for p in ("location", "rotation_euler", "scale"):
                obj.keyframe_insert(p, frame=f)
    # quaternion continuity
    sc.frame_set(fs)
    return {"target": obj.name, "action": act.name, "previous_action": old.name if old else None,
            "frames": [fs, fe], "fcurves": len(_fcurves(obj)), "constraints_removed": removed}


@recipe("rigging", tags=("nla", "actions", "strips", "sequence", "layer", "animation clips"))
def nla_push_and_stack(target=None, actions=None, mode="sequence", gap=0, blend_type="REPLACE", repeat=1,
                       track_name="Clips", clear_active=True) -> dict:
    '''Put actions on NLA strips: mode='sequence' lays them end-to-end on one track (gap frames between), mode='stack'
    puts each on its own track starting at frame 1 (blend_type e.g. ADD/COMBINE for layering). actions=None -> pushes the
    active action. Handles 5.x action slots. target None -> demo cube with two actions.'''
    if target is None:
        bpy.ops.mesh.primitive_cube_add()
        o = bpy.context.active_object
        names = []
        for nm, axis in (("MoveX", 0), ("MoveZ", 2)):
            _new_action(o, nm)
            o.location = (0, 0, 0)
            o.keyframe_insert("location", index=axis, frame=1)
            loc = list(o.location)
            loc[axis] = 2
            o.location = loc
            o.keyframe_insert("location", index=axis, frame=20)
            names.append(nm)
        target, actions = o.name, names
    obj = get_obj(target)
    obj.animation_data_create()
    ad = obj.animation_data
    if not actions:
        if ad.action is None:
            raise ValueError("no actions given and no active action")
        actions = [ad.action.name]
    acts = []
    for a in actions:
        if a not in bpy.data.actions:
            import difflib
            raise ValueError(f"action {a!r} not found. Close: {difflib.get_close_matches(a, list(bpy.data.actions.keys()))}")
        acts.append(bpy.data.actions[a])
    if mode not in ("sequence", "stack"):
        raise ValueError("mode must be sequence or stack")
    if clear_active:
        ad.action = None
    strips = []
    if mode == "sequence":
        tr = ad.nla_tracks.new()
        tr.name = track_name
        cur = 1
        for a in acts:
            s = tr.strips.new(a.name, int(cur), a)
            s.repeat = repeat
            s.blend_type = blend_type
            strips.append([tr.name, s.name, r(s.frame_start), r(s.frame_end)])
            cur = s.frame_end + gap
    else:
        for i, a in enumerate(acts):
            tr = ad.nla_tracks.new()
            tr.name = f"{track_name}_{a.name}"
            s = tr.strips.new(a.name, 1, a)
            s.repeat = repeat
            s.blend_type = blend_type if i else "REPLACE"
            strips.append([tr.name, s.name, r(s.frame_start), r(s.frame_end)])
    return {"target": obj.name, "tracks": len(ad.nla_tracks), "strips": strips,
            "slots": [getattr(s, "action_slot", None) and s.action_slot.identifier
                      for t in ad.nla_tracks for s in t.strips]}


@recipe("rigging", tags=("report", "action", "fcurves", "keys", "inspect", "animation"))
def action_report(target=None, max_bones=40) -> dict:
    '''Summarise an object's animation: active action + slot, frame range, F-curve count, keys and channels per bone
    (or per data path), modifiers (cycles), NLA tracks, shape-key and constraint animation. target None -> demo walk cycle.'''
    if target is None:
        target = procedural_walk_cycle()["armature"]
    obj = get_obj(target)
    ad = obj.animation_data
    out = {"target": obj.name, "action": None}
    if ad:
        out["nla_tracks"] = [{"name": t.name, "strips": [[s.name, s.action.name if s.action else None, r(s.frame_start), r(s.frame_end)]
                                                         for s in t.strips]} for t in ad.nla_tracks]
    if ad and ad.action:
        act = ad.action
        fcs = _fcurves(obj)
        per = {}
        mods = 0
        for fc in fcs:
            b = _bone_of_path(fc.data_path)
            key = b or fc.data_path
            chan = fc.data_path.rsplit(".", 1)[-1]
            d = per.setdefault(key, {"keys": 0, "channels": set()})
            d["keys"] = max(d["keys"], len(fc.keyframe_points))
            d["channels"].add(chan)
            mods += sum(1 for m in fc.modifiers if m.type == "CYCLES")
        out.update({"action": act.name, "slot": ad.action_slot.identifier if ad.action_slot else None,
                    "layered": bool(getattr(act, "is_action_layered", False)),
                    "frame_range": r(list(act.frame_range)), "fcurves": len(fcs), "cyclic_fcurves": mods,
                    "bones_animated": sum(1 for fc in fcs if _bone_of_path(fc.data_path)) and len({_bone_of_path(fc.data_path) for fc in fcs} - {None}),
                    "channels": {k: {"keys": v["keys"], "channels": sorted(v["channels"])} for k, v in list(per.items())[:max_bones]}})
    if obj.type == "MESH" and obj.data.shape_keys and obj.data.shape_keys.animation_data:
        out["shape_key_fcurves"] = len(_fcurves(obj.data.shape_keys))
    if obj.animation_data and obj.animation_data.drivers:
        out["drivers"] = [d.data_path for d in obj.animation_data.drivers][:20]
    out["all_actions"] = [a.name for a in bpy.data.actions][:30]
    return out


@recipe("rigging", tags=("interpolation", "cyclic", "loop", "extrapolation", "easing", "fcurve"))
def set_interpolation(target=None, interpolation="BEZIER", easing="AUTO", cyclic=None, extrapolation=None, bone=None,
                      data_path_contains=None) -> dict:
    '''Set keyframe interpolation (CONSTANT/LINEAR/BEZIER/SINE/...) and easing on all (or filtered) F-curves of a
    target's active action; cyclic=True/False adds/removes Cycles modifiers; extrapolation CONSTANT/LINEAR.
    bone / data_path_contains filter channels. target None -> demo cube with keys.'''
    if target is None:
        target = keyframe_transform()["target"]
    obj = get_obj(target)
    fcs = _fcurves(obj)
    if obj.type == "MESH" and obj.data.shape_keys:
        fcs += _fcurves(obj.data.shape_keys)
    if not fcs:
        raise ValueError(f"{obj.name!r} has no animation")
    bname = resolve_bone(obj, bone) if bone else None
    n = keys = 0
    for fc in fcs:
        if bname and _bone_of_path(fc.data_path) != bname:
            continue
        if data_path_contains and data_path_contains not in fc.data_path:
            continue
        for kp in fc.keyframe_points:
            kp.interpolation = interpolation
            kp.easing = easing
            keys += 1
        if extrapolation:
            fc.extrapolation = extrapolation
        if cyclic is True:
            _cyclic([fc])
        elif cyclic is False:
            for m in [m for m in fc.modifiers if m.type == "CYCLES"]:
                fc.modifiers.remove(m)
        fc.update()
        n += 1
    return {"target": obj.name, "fcurves": n, "keys": keys, "interpolation": interpolation, "cyclic": cyclic,
            "extrapolation": extrapolation}


@recipe("rigging", tags=("shape key", "blendshape", "blink", "morph", "facial", "keyframes"))
def shape_key_animate(target=None, name="Blink", offset=(0, 0, -0.03), region_min=None, region_max=None,
                      vertex_group=None, scale_toward_center=0.0, keys=None) -> dict:
    '''Create a shape key (blendshape, exported to Unity) by offsetting vertices in a region/vertex group, then keyframe
    its value. keys=[[frame, value], ...]; default is a blink (0 -> 1 -> 0 over 6 frames). region_min/max are
    object-space bounds; scale_toward_center (0..1) pinches toward the region centre. target None -> demo sphere 'Head'.'''
    if target is None:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.12, location=(0, 0, 1.6))
        o = bpy.context.active_object
        o.name = "Head"
        target = o.name
        region_min, region_max = (-0.1, -0.13, 0.0), (0.1, -0.05, 0.05)
    obj = _mesh(target)
    me = obj.data
    if keys is None:
        keys = [[1, 0.0], [4, 1.0], [7, 0.0]]
    if me.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    kb = me.shape_keys.key_blocks.get(name) or obj.shape_key_add(name=name, from_mix=False)
    vg = obj.vertex_groups.get(vertex_group) if vertex_group else None
    if vertex_group and vg is None:
        raise ValueError(f"vertex group {vertex_group!r} not found on {obj.name}")
    mn = Vector(region_min) if region_min else None
    mx = Vector(region_max) if region_max else None
    sel = []
    for v in me.vertices:
        w = 1.0
        if vg:
            w = next((g.weight for g in v.groups if g.group == vg.index), 0.0)
        if mn is not None and mx is not None and not all(mn[i] <= v.co[i] <= mx[i] for i in range(3)):
            w = 0.0
        if w > 0:
            sel.append((v.index, w))
    if not sel:
        raise ValueError("no vertices matched region/vertex_group")
    ctr = sum((me.vertices[i].co for i, _ in sel), Vector()) / len(sel)
    for i, w in sel:
        co = me.vertices[i].co
        kb.data[i].co = co + Vector(offset) * w + (ctr - co) * scale_toward_center * w
    for f, val in keys:
        kb.value = val
        kb.keyframe_insert("value", frame=f)
    return {"mesh": obj.name, "shape_key": kb.name, "vertices_moved": len(sel),
            "shape_keys": [k.name for k in me.shape_keys.key_blocks], "keys": len(keys)}


@recipe("rigging", tags=("driver", "expression", "property", "link", "automation"))
def driver_add(target=None, data_path="rotation_euler", index=2, expression="var", variables=None) -> dict:
    '''Add a scripted-expression driver. variables=[{"name":"var","type":"TRANSFORMS","target":"Empty",
    "transform_type":"LOC_X","bone":None,"space":"WORLD_SPACE"} | {"name":"v","type":"SINGLE_PROP","target":"Cube",
    "data_path":"location[0]"}]. data_path may point to a pose bone ('pose.bones["Jaw"].rotation_euler') or a shape key
    on a mesh target ('key_blocks["Blink"].value' -> driven on the mesh's Key). target None -> demo cube driven by an Empty's X.'''
    if target is None:
        bpy.ops.mesh.primitive_cube_add()
        target = bpy.context.active_object.name
        e = _link(bpy.data.objects.new("DriverCtrl", None))
        e.location.x = 0.5
        variables = [{"name": "var", "type": "TRANSFORMS", "target": e.name, "transform_type": "LOC_X"}]
        expression = "var * 2"
    obj = get_obj(target)
    owner = obj
    if data_path.startswith("key_blocks"):
        if obj.type != "MESH" or not obj.data.shape_keys:
            raise ValueError("shape-key driver needs a mesh with shape keys")
        owner = obj.data.shape_keys
    try:
        fc = owner.driver_add(data_path, index) if index is not None and index >= 0 else owner.driver_add(data_path)
    except TypeError:
        fc = owner.driver_add(data_path)
    drv = fc.driver
    drv.type = "SCRIPTED"
    drv.expression = expression
    for v in variables or []:
        var = drv.variables.new()
        var.name = v.get("name", "var")
        vtype = v.get("type", "SINGLE_PROP")
        var.type = vtype
        tgt = var.targets[0]
        tobj = get_obj(v["target"])
        tgt.id = tobj if vtype != "SINGLE_PROP" or not v.get("id_type") else tobj
        if vtype == "TRANSFORMS":
            tgt.transform_type = v.get("transform_type", "LOC_X")
            tgt.transform_space = v.get("space", "WORLD_SPACE")
            if v.get("bone"):
                tgt.bone_target = resolve_bone(tobj, v["bone"])
        elif vtype == "SINGLE_PROP":
            tgt.data_path = v["data_path"]
        else:
            raise ValueError("variable type must be TRANSFORMS or SINGLE_PROP")
    bpy.context.view_layer.update()
    val = None
    try:
        cur = owner.path_resolve(data_path)
        val = r(cur[index] if index is not None and index >= 0 and hasattr(cur, "__getitem__") else cur, 4)
    except Exception:
        pass
    return {"target": obj.name, "data_path": data_path, "index": index, "expression": expression,
            "variables": [v.name for v in drv.variables], "valid": drv.is_valid, "current_value": val}


@recipe("rigging", tags=("retarget", "mocap", "transfer", "animation", "mixamo", "bake"))
def retarget_simple(source=None, target=None, bone_map=None, frame_start=None, frame_end=None, transfer_root_motion=True,
                    action_name=None) -> dict:
    '''Copy animation from a source armature to a target armature by bone mapping (auto: matched via canonical humanoid
    names across blender/mixamo/unity conventions) and bake keys on the target. Rotations are transferred as
    armature-space deltas from rest, so both rigs should share a similar rest pose (T-pose->T-pose). Hips translation
    is scaled by rig height ratio. source/target None -> demo walk on a blender-named rig retargeted to a mixamo rig.'''
    sc = bpy.context.scene
    if source is None:
        source = procedural_walk_cycle(armature=_demo_rig("SourceRig"))["armature"]
        target = biped_armature(name="TargetRig", naming="mixamo", height=1.6, location=(1.5, 0, 0))["armature"]
        frame_start, frame_end = 1, 24
    src, tgt = _armature(source), _armature(target)
    if bone_map is None:
        cs, ct = canon_map(src), canon_map(tgt)
        bone_map = {cs[k]: ct[k] for k in cs if k in ct}
    else:
        bone_map = {resolve_bone(src, a): resolve_bone(tgt, b) for a, b in bone_map.items()}
    if not bone_map:
        raise ValueError("no bones could be mapped; pass bone_map={source_bone: target_bone}")
    src_act = src.animation_data.action if src.animation_data else None
    if frame_start is None or frame_end is None:
        rng = src_act.frame_range if src_act else (sc.frame_start, sc.frame_end)
        frame_start, frame_end = int(rng[0]), int(rng[1])
    inv = {v: k for k, v in bone_map.items()}
    hs = max(b.tail_local.z for b in src.data.bones)
    ht = max(b.tail_local.z for b in tgt.data.bones)
    ratio = ht / hs if hs > 0 else 1.0
    order = []
    stack = [b for b in tgt.data.bones if b.parent is None]
    while stack:
        b = stack.pop(0)
        order.append(b)
        stack.extend(b.children)
    root_src = canon_map(src).get("hips")
    frames = list(range(frame_start, frame_end + 1))
    baked = {}
    for f in frames:
        sc.frame_set(f)
        posed = {}
        fr = {}
        for b in order:
            rest = b.matrix_local.to_quaternion()
            par_rest = b.parent.matrix_local.to_quaternion() if b.parent else Quaternion()
            par_posed = posed[b.parent.name] if b.parent else Quaternion()
            if b.name in inv:
                sb = src.pose.bones[inv[b.name]]
                delta = sb.matrix.to_quaternion() @ sb.bone.matrix_local.to_quaternion().inverted()
                want = delta @ rest
                q = rest.inverted() @ par_rest @ par_posed.inverted() @ want
                posed[b.name] = want
                loc = None
                if transfer_root_motion and inv[b.name] == root_src:
                    d = (sb.head - sb.bone.head_local) * ratio
                    loc = b.matrix_local.to_3x3().inverted() @ d  # parent pose ignored (hips/root)
                fr[b.name] = (q, loc)
            else:
                posed[b.name] = par_posed @ par_rest.inverted() @ rest
        baked[f] = fr
    act = _new_action(tgt, action_name or ((src_act.name if src_act else "Anim") + "_retarget"))
    for f in frames:
        for name, (q, loc) in baked[f].items():
            pb = tgt.pose.bones[name]
            _key_q(pb, q, f)
            if loc is not None:
                pb.location = loc
                pb.keyframe_insert("location", frame=f, group=name)
    sc.frame_set(frame_start)
    return {"source": src.name, "target": tgt.name, "action": act.name, "mapped_bones": len(bone_map),
            "frames": [frame_start, frame_end], "height_ratio": r(ratio), "fcurves": len(_fcurves(tgt)),
            "unmapped_target_bones": [b.name for b in tgt.data.bones if b.name not in inv][:20]}


@recipe("rigging", tags=("root motion", "in place", "locomotion", "unity", "hips"))
def root_motion_extract(armature=None, mode="in_place", hips=None, root=None, axes="XY", frame_start=None, frame_end=None) -> dict:
    '''Remove (mode='in_place') or move (mode='to_root' / 'to_object') the horizontal travel of the hips.
    to_root keys the travel on a root bone (created via add_root_bone if missing) for Unity root motion; to_object keys
    the armature object. axes = armature-space axes treated as travel ('XY' default, add 'Z' for jumps).
    armature None -> demo biped whose hips travel 2 m forward over 24 frames.'''
    sc = bpy.context.scene
    if armature is None:
        armature = _demo_rig()
        o = bpy.data.objects[armature]
        hp = o.pose.bones[resolve_bone(o, "hips")]
        for f, y in ((1, 0.0), (24, -2.0)):
            hp.location = _vec_local(hp, (0, y, 0))
            hp.keyframe_insert("location", frame=f, group=hp.name)
    obj = _armature(armature)
    if mode not in ("in_place", "to_root", "to_object"):
        raise ValueError("mode must be in_place, to_root or to_object")
    hname = resolve_bone(obj, hips or "hips")
    act = obj.animation_data.action if obj.animation_data else None
    if act is None:
        raise ValueError("armature has no active action")
    fs = frame_start if frame_start is not None else int(act.frame_range[0])
    fe = frame_end if frame_end is not None else int(act.frame_range[1])
    ax = [i for i, a in enumerate("XYZ") if a in axes.upper()]
    hp = obj.pose.bones[hname]
    samples = {}
    for f in range(fs, fe + 1):
        sc.frame_set(f)
        samples[f] = (hp.head.copy(), hp.location.copy())
    origin = samples[fs][0]
    rname = None
    if mode == "to_root":
        rname = root or "Root"
        if rname not in obj.data.bones:
            add_root_bone(obj.name, rname)
        hp = obj.pose.bones[hname]
    R = hp.bone.matrix_local.to_3x3().inverted()
    for f, (head, loc) in samples.items():
        d = Vector((0, 0, 0))
        for i in ax:
            d[i] = head[i] - origin[i]
        hp.location = loc - R @ d
        hp.keyframe_insert("location", frame=f, group=hname)
        if mode == "to_root":
            rb = obj.pose.bones[rname]
            rb.location = rb.bone.matrix_local.to_3x3().inverted() @ d
            rb.keyframe_insert("location", frame=f, group=rname)
    if mode == "to_object":
        base = obj.location.copy()
        for f, (head, loc) in samples.items():
            d = Vector((0, 0, 0))
            for i in ax:
                d[i] = head[i] - origin[i]
            obj.location = base + obj.matrix_world.to_3x3() @ d
            obj.keyframe_insert("location", frame=f)
        obj.location = base
    sc.frame_set(fs)
    end_d = samples[fe][0] - origin
    return {"armature": obj.name, "mode": mode, "hips": hname, "root": rname, "frames": [fs, fe],
            "travel_extracted": r([end_d[i] if i in ax else 0.0 for i in range(3)])}


@recipe("rigging", tags=("fps", "frame range", "scene", "timeline", "export"))
def set_fps_and_range(fps=30, frame_start=1, frame_end=None, fit_to=None, fps_base=1.0) -> dict:
    '''Set scene FPS and frame range. fit_to=object name uses its active action's frame range (handy before exporting a
    clip). Unity default is 30 fps (XR apps often 60/72/90 for playback, but clips are resampled on import).'''
    sc = bpy.context.scene
    if fps <= 0:
        raise ValueError("fps must be > 0")
    sc.render.fps = int(round(fps))
    sc.render.fps_base = fps_base
    if fit_to:
        o = get_obj(fit_to)
        act = o.animation_data.action if o.animation_data else None
        if act is None:
            raise ValueError(f"{fit_to!r} has no active action")
        frame_start, frame_end = int(math.floor(act.frame_range[0])), int(math.ceil(act.frame_range[1]))
    sc.frame_start = int(frame_start)
    if frame_end is not None:
        if frame_end < frame_start:
            raise ValueError("frame_end < frame_start")
        sc.frame_end = int(frame_end)
    return {"fps": sc.render.fps, "fps_effective": r(sc.render.fps / sc.render.fps_base, 3),
            "frame_start": sc.frame_start, "frame_end": sc.frame_end,
            "duration_s": r((sc.frame_end - sc.frame_start + 1) / (sc.render.fps / sc.render.fps_base), 3)}
