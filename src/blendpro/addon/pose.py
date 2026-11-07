"""Pose capture for MediaPipe: render a character view, then lift 2D landmarks into 3D joints inside the mesh."""
import math
import os
import tempfile
import bpy
import mathutils
from mathutils.bvhtree import BVHTree
from .util import r, get_obj, world_bbox

# MediaPipe Pose 33-landmark topology (names follow mediapipe PoseLandmark, lower-case)
NAMES = ["nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye", "right_eye_outer",
         "left_ear", "right_ear", "mouth_left", "mouth_right", "left_shoulder", "right_shoulder", "left_elbow",
         "right_elbow", "left_wrist", "right_wrist", "left_pinky", "right_pinky", "left_index", "right_index",
         "left_thumb", "right_thumb", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
         "left_heel", "right_heel", "left_foot_index", "right_foot_index"]
BODY_EDGES = [("left_shoulder", "right_shoulder"), ("left_hip", "right_hip"), ("left_shoulder", "left_hip"),
              ("right_shoulder", "right_hip"), ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
              ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"), ("left_hip", "left_knee"),
              ("left_knee", "left_ankle"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
              ("left_ankle", "left_heel"), ("left_heel", "left_foot_index"), ("left_ankle", "left_foot_index"),
              ("right_ankle", "right_heel"), ("right_heel", "right_foot_index"), ("right_ankle", "right_foot_index"),
              ("left_wrist", "left_index"), ("right_wrist", "right_index"), ("neck", "nose"), ("neck", "pelvis"),
              ("left_eye", "right_eye"), ("nose", "left_eye"), ("nose", "right_eye")]
VIEWS = {  # camera looks along d; character convention: faces -Y, so 'front' camera sits at -Y looking +Y
    "front": ((0, 1, 0), (0, 0, 1)), "back": ((0, -1, 0), (0, 0, 1)),
    "left": ((1, 0, 0), (0, 0, 1)), "right": ((-1, 0, 0), (0, 0, 1)),
}


def _targets(objects):
    objs = []
    for n in objects:
        o = get_obj(n)
        objs.append(o)
        objs.extend(c for c in o.children_recursive)
    from .util import real_meshes
    meshes = real_meshes(objs)
    if not meshes:
        raise ValueError("no mesh objects among targets (pass the character mesh or its root)")
    return meshes


def _bounds(meshes, dg):
    mins, maxs = [], []
    for o in meshes:
        bb = world_bbox(o, dg)
        if bb:
            mins.append(bb[0]); maxs.append(bb[1])
    mn = mathutils.Vector([min(v[i] for v in mins) for i in range(3)])
    mx = mathutils.Vector([max(v[i] for v in maxs) for i in range(3)])
    return mn, mx


def _pose_render(objects, view="front", size=768, engine="workbench", frame=None, filepath=None):
    """Render an orthographic view of the character for landmark detection; returns path + camera mapping."""
    scene = bpy.context.scene
    if frame is not None:
        scene.frame_set(int(frame))
    dg = bpy.context.evaluated_depsgraph_get()
    meshes = _targets(objects)
    mn, mx = _bounds(meshes, dg)
    center = (mn + mx) / 2
    d = mathutils.Vector(VIEWS[view][0]).normalized()
    up = mathutils.Vector(VIEWS[view][1])
    z = -d
    x = up.cross(z).normalized()
    y = z.cross(x)
    rot = mathutils.Matrix((x, y, z)).transposed().to_quaternion()
    ext = mx - mn
    perp = [i for i in range(3) if abs(d[i]) < 0.5]
    ortho = max(ext[i] for i in perp) * 1.12
    depth = (mx - mn).length
    rd = scene.render
    saved = dict(engine=rd.engine, rx=rd.resolution_x, ry=rd.resolution_y, pct=rd.resolution_percentage,
                 cam=scene.camera, path=rd.filepath, fmt=rd.image_settings.file_format, film=rd.film_transparent)
    sh = scene.display.shading
    saved_sh = {k: getattr(sh, k) for k in ("light", "color_type", "show_cavity", "show_object_outline")}
    keep = {o.name for o in meshes}
    hidden = []
    for o in scene.objects:
        if o.name not in keep and not o.hide_render and o.type not in {"LIGHT"} and o not in [m.parent for m in meshes]:
            o.hide_render = True
            hidden.append(o)
    cd = bpy.data.cameras.new(".bp_pose_cam")
    cd.type = "ORTHO"
    cd.ortho_scale = ortho
    cd.clip_start = depth * 0.01
    cd.clip_end = depth * 10
    co = bpy.data.objects.new(".bp_pose_cam", cd)
    scene.collection.objects.link(co)
    co.rotation_mode = "QUATERNION"
    co.rotation_quaternion = rot
    co.location = center - d * depth * 2
    path = filepath or os.path.join(tempfile.gettempdir(), f"blendpro_pose_{view}_{frame or scene.frame_current}.png")
    try:
        from .util import eevee_engine_id
        rd.engine = {"workbench": "BLENDER_WORKBENCH", "eevee": eevee_engine_id(), "cycles": "CYCLES"}.get(engine, "BLENDER_WORKBENCH")
        if rd.engine == "BLENDER_WORKBENCH":
            sh.light = "STUDIO"
            has_tex = any(n.bl_idname == "ShaderNodeTexImage" for o in meshes for s in o.material_slots
                          if s.material and s.material.node_tree for n in s.material.node_tree.nodes)
            sh.color_type = "TEXTURE" if has_tex else "MATERIAL"
            sh.show_cavity = True
            sh.show_object_outline = False
        rd.resolution_x = rd.resolution_y = int(size)
        rd.resolution_percentage = 100
        rd.film_transparent = False
        rd.image_settings.file_format = "PNG"
        rd.filepath = path
        scene.camera = co
        bpy.ops.render.render(write_still=True)
    finally:
        for o in hidden:
            o.hide_render = False
        rd.engine, rd.resolution_x, rd.resolution_y = saved["engine"], saved["rx"], saved["ry"]
        rd.resolution_percentage, scene.camera, rd.filepath = saved["pct"], saved["cam"], saved["path"]
        rd.image_settings.file_format, rd.film_transparent = saved["fmt"], saved["film"]
        for k, v in saved_sh.items():
            try:
                setattr(sh, k, v)
            except Exception:
                pass
        bpy.data.objects.remove(co)
        bpy.data.cameras.remove(cd)
    return {"filepath": path, "frame": scene.frame_current,
            "camera": {"center": list(center), "right": list(x), "up": list(y), "dir": list(d),
                       "ortho_scale": ortho, "depth": depth, "view": view}}


def _bvh(meshes, dg):
    verts, polys = [], []
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        off = len(verts)
        mw = o.matrix_world
        verts.extend(mw @ v.co for v in me.vertices)
        polys.extend(tuple(off + i for i in p.vertices) for p in me.polygons)
        ev.to_mesh_clear()
    return BVHTree.FromPolygons(verts, polys)


def _through(bvh, origin, d, max_dist):
    """Entry/exit points of a ray through the mesh; returns (entry, exit) or None."""
    hits = []
    o = origin.copy()
    travelled = 0.0
    for _ in range(16):
        loc, nrm, idx, dist = bvh.ray_cast(o, d, max_dist - travelled)
        if loc is None:
            break
        hits.append(loc)
        travelled += dist + 1e-4
        o = loc + d * 1e-4
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0], hits[0]
    return hits[0], hits[1]


def _pose_lift(landmarks, camera, objects, create="lines", min_visibility=0.5, name="MP", frame=None):
    """landmarks: list of 33 [x, y, z, visibility] (MediaPipe normalized image coords, origin top-left)."""
    scene = bpy.context.scene
    if frame is not None:
        scene.frame_set(int(frame))
    dg = bpy.context.evaluated_depsgraph_get()
    meshes = _targets(objects)
    bvh = _bvh(meshes, dg)
    C = mathutils.Vector(camera["center"])
    R = mathutils.Vector(camera["right"])
    U = mathutils.Vector(camera["up"])
    D = mathutils.Vector(camera["dir"])
    S = camera["ortho_scale"]
    depth = camera["depth"]
    joints, quality = {}, {}
    pending = []
    for i, lm in enumerate(landmarks[:33]):
        x, y, _z, vis = (list(lm) + [1.0])[:4]
        nm = NAMES[i]
        if vis < min_visibility:
            quality[nm] = f"low visibility {round(vis, 2)}"
            continue
        p = C + (x - 0.5) * S * R + (0.5 - y) * S * U
        hit = _through(bvh, p - D * depth * 2, D, depth * 4)
        if hit:
            joints[nm] = (hit[0] + hit[1]) / 2
            quality[nm] = "inside mesh" if (hit[0] - hit[1]).length > 1e-6 else "surface"
        else:
            pending.append((nm, p))
    # rays that missed the silhouette: snap to nearest surface point on the mesh, then take the mean depth
    if joints:
        mean_depth = sum((v - C).dot(D) for v in joints.values()) / len(joints)
    else:
        mean_depth = 0.0
    for nm, p in pending:
        q = p + D * ((C - p).dot(D) + mean_depth)
        joints[nm] = q
        quality[nm] = "estimated (outside silhouette)"
    # derived joints for rigging
    def mid(a, b):
        return (joints[a] + joints[b]) / 2 if a in joints and b in joints else None
    pelvis = mid("left_hip", "right_hip")
    neck = mid("left_shoulder", "right_shoulder")
    if pelvis is not None:
        joints["pelvis"] = pelvis
    if neck is not None:
        joints["neck"] = neck
    if pelvis is not None and neck is not None:
        joints["spine"] = pelvis.lerp(neck, 0.35)
        joints["chest"] = pelvis.lerp(neck, 0.7)
    if "nose" in joints and neck is not None:
        mn, mx = _bounds(meshes, dg)
        top = joints["nose"].copy()
        top.z = mx.z
        joints["head_top"] = top
    created = []
    coll = None
    if create and create != "none":
        cname = f"{name}_Pose"
        coll = bpy.data.collections.get(cname)
        if coll is None:
            coll = bpy.data.collections.new(cname)
            scene.collection.children.link(coll)
        for o in list(coll.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        size = depth * 0.012
        if create in ("lines", "empties", "both"):
            keys = [k for k in joints]
            idx = {k: i for i, k in enumerate(keys)}
            me = bpy.data.meshes.new(f"{name}_Skeleton")
            edges = [(idx[a], idx[b]) for a, b in BODY_EDGES if a in idx and b in idx]
            me.from_pydata([tuple(joints[k]) for k in keys], edges, [])
            sk = bpy.data.objects.new(f"{name}_Skeleton", me)
            sk.show_in_front = True
            sk.display_type = "WIRE"
            sk.color = (1.0, 0.3, 0.05, 1.0)
            coll.objects.link(sk)
            sk["blendpro_pose_joints"] = keys
            created.append(sk.name)
        if create in ("empties", "both"):
            for k, v in joints.items():
                e = bpy.data.objects.new(f"{name}_{k}", None)
                e.empty_display_type = "SPHERE"
                e.empty_display_size = size
                e.location = v
                e.show_in_front = True
                coll.objects.link(e)
                created.append(e.name)
    return {"joints": {k: r(list(v), 3) for k, v in joints.items()}, "quality": quality,
            "metrics": _metrics(joints), "created": created, "collection": coll.name if coll else None}


def _angle(a, b, c):
    v1, v2 = a - b, c - b
    if v1.length < 1e-6 or v2.length < 1e-6:
        return None
    return round(math.degrees(v1.angle(v2)), 1)


def _metrics(j):
    m = {}
    g = j.get

    def dist(a, b):
        return round((j[a] - j[b]).length, 3) if a in j and b in j else None
    for side in ("left", "right"):
        s = side
        if all(k in j for k in (f"{s}_shoulder", f"{s}_elbow", f"{s}_wrist")):
            m[f"{s}_elbow_angle"] = _angle(j[f"{s}_shoulder"], j[f"{s}_elbow"], j[f"{s}_wrist"])
            upper = j[f"{s}_elbow"] - j[f"{s}_shoulder"]
            horiz = math.degrees(math.asin(max(-1, min(1, upper.normalized().z))))
            m[f"{s}_upper_arm_elevation_deg"] = round(horiz, 1)  # 0 = horizontal (T-pose), ~-45 = A-pose
        if all(k in j for k in (f"{s}_hip", f"{s}_knee", f"{s}_ankle")):
            m[f"{s}_knee_angle"] = _angle(j[f"{s}_hip"], j[f"{s}_knee"], j[f"{s}_ankle"])
        m[f"{s}_upper_arm_len"] = dist(f"{s}_shoulder", f"{s}_elbow")
        m[f"{s}_forearm_len"] = dist(f"{s}_elbow", f"{s}_wrist")
        m[f"{s}_thigh_len"] = dist(f"{s}_hip", f"{s}_knee")
        m[f"{s}_shin_len"] = dist(f"{s}_knee", f"{s}_ankle")
        if f"{s}_ankle" in j:
            m[f"{s}_ankle_height"] = round(j[f"{s}_ankle"].z, 3)
    m["shoulder_width"] = dist("left_shoulder", "right_shoulder")
    m["hip_width"] = dist("left_hip", "right_hip")
    if "pelvis" in j and "neck" in j:
        spine = j["neck"] - j["pelvis"]
        m["spine_lean_deg"] = round(math.degrees(spine.angle(mathutils.Vector((0, 0, 1)))), 1)
    if "left_shoulder" in j and "right_shoulder" in j:
        across = j["left_shoulder"] - j["right_shoulder"]  # points to the subject's left
        facing = across.cross(mathutils.Vector((0, 0, 1)))  # subject forward (left x up)
        if facing.length > 1e-6:
            facing.normalize()
            m["facing_dir_xy"] = r([facing.x, facing.y], 2)
    ea = [m.get("left_upper_arm_elevation_deg"), m.get("right_upper_arm_elevation_deg")]
    if all(e is not None for e in ea):
        avg = sum(ea) / 2
        m["arm_pose"] = "T-pose" if avg > -15 else ("A-pose" if avg > -60 else "arms down")
    for a, b in (("upper_arm_len", "upper_arm_len"), ("thigh_len", "thigh_len")):
        la, rb = m.get(f"left_{a}"), m.get(f"right_{b}")
        if la and rb:
            m[f"{a}_asymmetry_pct"] = round(abs(la - rb) / max(la, rb) * 100, 1)
    return {k: v for k, v in m.items() if v is not None}


def _armatures_for(objects):
    arms = set()
    for n in objects:
        o = get_obj(n)
        for x in [o] + list(o.children_recursive) + ([o.parent] if o.parent else []):
            if x.type == "ARMATURE":
                arms.add(x.data)
            for m in getattr(x, "modifiers", []):
                if m.type == "ARMATURE" and m.object:
                    arms.add(m.object.data)
    return arms


class _RestPose:
    """Temporarily show armatures in rest position (T/A pose) - far better for landmark detection and rigging."""

    def __init__(self, objects, enabled):
        self.arms = _armatures_for(objects) if enabled else set()
        self.saved = {}

    def __enter__(self):
        for a in self.arms:
            self.saved[a] = a.pose_position
            a.pose_position = "REST"
        bpy.context.view_layer.update()

    def __exit__(self, *exc):
        for a, v in self.saved.items():
            a.pose_position = v
        bpy.context.view_layer.update()


def pose_render(objects, view="front", size=768, engine="workbench", frame=None, filepath=None, rest_pose=False):
    with _RestPose(objects, rest_pose):
        return _pose_render(objects, view, size, engine, frame, filepath)


def pose_lift(landmarks, camera, objects, create="lines", min_visibility=0.5, name="MP", frame=None, rest_pose=False):
    with _RestPose(objects, rest_pose):
        return _pose_lift(landmarks, camera, objects, create, min_visibility, name, frame)
