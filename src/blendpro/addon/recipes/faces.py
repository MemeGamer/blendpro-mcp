"""Cute 2D face expressions for stylised characters: a texture-atlas face decal driven by one integer.

The bundled set (assets/faces/Face_1..50.png + faces.json) is black ink on a transparent 1024 canvas, all in
one shared layout (eyes at y~516, mouth below, centred), so swapping the face never makes features jump.

    face_expression_setup  -> decal on the head + atlas material, property "expression" picks the face
    face_expression_set    -> set / keyframe one expression by name ("happy", "angry", "uwu"...) or index
    face_expression_animate-> a timeline of expressions + automatic blinks (uses each face's "blink" partner)
    face_atlas_build       -> pack a folder of Face_N.png into one atlas (also handy for Unity / WebXR)
    face_sheet_import      -> slice an AI-generated grid sheet of new faces into Face_N.png in the same layout

The face index lives on the decal object as a custom property; the shader reads it with an Attribute node
(Object), so it works in EEVEE and Cycles, keyframes with CONSTANT interpolation, and needs no drivers."""
import json
import math
import os
import random
import re
import tempfile

import bmesh
import bpy
import mathutils
import numpy as np

from . import recipe
from ..util import get_obj, r

ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "faces")
LAYOUT = {"canvas": 1024, "eye_y": 516, "eye_width": 205, "center_x": 512}


# ------------------------------------------------------------------ catalog
def _faces_dir(faces_dir):
    d = bpy.path.abspath(faces_dir) if faces_dir else ASSET_DIR
    if not os.path.isdir(d):
        raise FileNotFoundError(f"faces folder not found: {d}")
    return d


def _face_files(d):
    out = {}
    for f in os.listdir(d):
        m = re.match(r"^(?:face)[_\- ]?(\d+)\.png$", f, re.I)
        if m:
            out[int(m.group(1))] = os.path.join(d, f)
    if not out:
        raise ValueError(f"no Face_N.png files in {d}")
    return dict(sorted(out.items()))


def _catalog(d):
    p = os.path.join(d, "faces.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return {int(k): v for k, v in data.get("faces", {}).items()}
    return {}


def _resolve(expr, catalog, n):
    if isinstance(expr, (int, float)) or (isinstance(expr, str) and expr.isdigit()):
        i = int(expr)
    else:
        key = str(expr).strip().lower().replace(" ", "_").replace("-", "_")
        hits = [k for k, v in catalog.items() if v.get("name") == key]
        if not hits:
            hits = [k for k, v in catalog.items() if key in v.get("tags", []) or key in v.get("name", "")]
        if not hits:
            names = sorted(v.get("name") for v in catalog.values())
            raise ValueError(f"unknown expression {expr!r}. Known: {', '.join(names)}")
        i = hits[0]
    if not 1 <= i <= n:
        raise ValueError(f"expression index {i} out of range 1..{n}")
    return i


# ------------------------------------------------------------------ image helpers (bpy + numpy only)
def _load_rgba(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    a = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(h, w, 4)  # bottom row first


def _resize(arr, w, h):
    """Resize an (H, W, 4) float array with Blender's image scaler."""
    ih, iw = arr.shape[:2]
    img = bpy.data.images.new("_bp_resize", iw, ih, alpha=True, float_buffer=True)
    img.pixels.foreach_set(arr.astype(np.float32).ravel())
    img.scale(max(1, int(w)), max(1, int(h)))
    out = np.empty(int(w) * int(h) * 4, dtype=np.float32)
    img.pixels.foreach_get(out)
    bpy.data.images.remove(img)
    return out.reshape(int(h), int(w), 4)


def _save_png(arr, path):
    h, w = arr.shape[:2]
    img = bpy.data.images.new("_bp_save", w, h, alpha=True)
    img.pixels.foreach_set(np.clip(arr, 0, 1).astype(np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


# ------------------------------------------------------------------ recipes
@recipe("rigging", tags=("face", "expression", "atlas", "texture", "unity", "webxr"))
def face_atlas_build(faces_dir=None, out_path=None, cell=256, cols=8, pad=8, pack=True, name="FaceAtlas") -> dict:
    """Pack Face_N.png files into one square-ish atlas image (index N -> cell N-1, row-major from the top-left).
    faces_dir: default = the bundled 50-face set. cell: pixels per face; pad keeps mipmaps from bleeding.
    Returns the image name, grid (cols x rows) and the face count. The atlas is packed into the .blend
    (pack=True) and also written to out_path (default: temp dir) so it can be reused in Unity/WebXR."""
    d = _faces_dir(faces_dir)
    files = _face_files(d)
    n = max(files)
    rows = math.ceil(n / cols)
    W, H = cols * cell, rows * cell
    atlas = np.zeros((H, W, 4), dtype=np.float32)  # bottom row first (Blender order)
    for idx, path in files.items():
        a = _load_rgba(path)
        a = _resize(a, cell - 2 * pad, cell - 2 * pad)
        k = idx - 1
        c, rr = k % cols, k // cols
        y0 = H - (rr + 1) * cell + pad
        x0 = c * cell + pad
        atlas[y0:y0 + cell - 2 * pad, x0:x0 + cell - 2 * pad] = a
    out_path = bpy.path.abspath(out_path) if out_path else os.path.join(tempfile.gettempdir(), f"{name}.png")
    _save_png(atlas, out_path)
    old = bpy.data.images.get(name)
    if old:
        bpy.data.images.remove(old)
    img = bpy.data.images.load(out_path)
    img.name = name
    img.colorspace_settings.name = "sRGB"
    img.alpha_mode = "STRAIGHT"
    if pack:
        img.pack()
    return {"image": img.name, "size": [W, H], "grid": [cols, rows], "faces": len(files), "max_index": n,
            "file": out_path, "packed": bool(pack)}


def _face_material(name, atlas, cols, rows, n, feature_color, emission):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial"); out.location = (900, 0)
    uv = N.new("ShaderNodeTexCoord"); uv.location = (-900, 200)
    attr = N.new("ShaderNodeAttribute"); attr.location = (-900, -150)
    attr.attribute_type = "OBJECT"; attr.attribute_name = "expression"

    def math_node(op, a=None, b=None, loc=(0, 0), v1=None):
        m = N.new("ShaderNodeMath"); m.operation = op; m.location = loc
        if a is not None:
            L.new(a, m.inputs[0])
        if v1 is not None:
            m.inputs[1].default_value = v1
        if b is not None:
            L.new(b, m.inputs[1])
        return m
    clampi = math_node("MAXIMUM", attr.outputs["Fac"], loc=(-700, -150), v1=1.0)
    clampi = math_node("MINIMUM", clampi.outputs[0], loc=(-560, -150), v1=float(n))
    rnd = math_node("ROUND", clampi.outputs[0], loc=(-420, -150))
    k = math_node("SUBTRACT", rnd.outputs[0], loc=(-280, -150), v1=1.0)
    col = math_node("FLOORED_MODULO", k.outputs[0], loc=(-140, -60), v1=float(cols))
    row = math_node("DIVIDE", k.outputs[0], loc=(-140, -240), v1=float(cols))
    row = math_node("FLOOR", row.outputs[0], loc=(0, -240))
    vrow = math_node("SUBTRACT", None, row.outputs[0], loc=(140, -240))
    vrow.inputs[0].default_value = float(rows - 1)
    comb = N.new("ShaderNodeCombineXYZ"); comb.location = (280, -150)
    L.new(col.outputs[0], comb.inputs["X"]); L.new(vrow.outputs[0], comb.inputs["Y"])
    add = N.new("ShaderNodeVectorMath"); add.operation = "ADD"; add.location = (420, 100)
    L.new(uv.outputs["UV"], add.inputs[0]); L.new(comb.outputs[0], add.inputs[1])
    scl = N.new("ShaderNodeVectorMath"); scl.operation = "MULTIPLY"; scl.location = (560, 100)
    scl.inputs[1].default_value = (1.0 / cols, 1.0 / rows, 1.0)
    L.new(add.outputs[0], scl.inputs[0])
    tex = N.new("ShaderNodeTexImage"); tex.location = (700, 300)
    tex.image = atlas; tex.extension = "CLIP"; tex.interpolation = "Linear"
    L.new(scl.outputs[0], tex.inputs["Vector"])
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (700, -100)
    bsdf.inputs["Base Color"].default_value = feature_color
    bsdf.inputs["Roughness"].default_value = 1.0
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.0  # ink should read as flat black
    if emission > 0:
        bsdf.inputs["Emission Color"].default_value = feature_color
        bsdf.inputs["Emission Strength"].default_value = emission
    transp = N.new("ShaderNodeBsdfTransparent"); transp.location = (700, -400)
    mix = N.new("ShaderNodeMixShader"); mix.location = (900, -150)
    L.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    L.new(transp.outputs[0], mix.inputs[1]); L.new(bsdf.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], out.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "HASHED"
        except Exception:
            pass
    return mat


@recipe("rigging", tags=("face", "expression", "decal", "cute", "stylized", "character", "emotion"))
def face_expression_setup(target=None, faces_dir=None, expression="happy", size=None, height=0.55, subdivisions=24,
                          offset=None, feature_color=(0.02, 0.02, 0.025, 1.0), emission=0.0, name="FaceDecal",
                          cell=256, follow_rig=True) -> dict:
    """Give a stylised character a swappable 2D face: a subdivided decal plane on the front (-Y side) of
    `target`, projected onto its surface (Shrinkwrap, so it follows the head), parented to it, with an atlas
    material that shows face N where N = the decal's custom property "expression" (int, keyframeable).
    target=None builds a demo head (sphere). size: face canvas width in metres (default = head width; the features fill ~60% of it);
    height: eye line as a fraction of the head's height from the bottom. offset (default 0.6% of the head
    width) keeps the head's facet edges from poking through the decal as tiny skin-coloured specks. Use face_expression_set /
    face_expression_animate to drive it. follow_rig: if the head is armature-deformed (e.g. squash_stretch_rig),
    the decal copies the nearest head weights and gets the same Armature modifier, so the face rides the rig. Faces: the bundled 50 (happy, sad, angry, uwu, star_eyes...)."""
    d = _faces_dir(faces_dir)
    catalog = _catalog(d)
    if target is None:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=0.5, location=(0, 0, 0.5))
        head = bpy.context.active_object
        head.name = "Head"
        for p in head.data.polygons:
            p.use_smooth = True
        skin = bpy.data.materials.new("Skin")
        skin.use_nodes = True
        skin.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (1.0, 0.75, 0.62, 1.0)
        head.data.materials.append(skin)
    else:
        head = get_obj(target)
    # measure and copy weights in the REST pose: on a posed/animated rig the bounds would be the current pose's
    rig_obj = next((m.object for m in head.modifiers if m.type == "ARMATURE" and m.object), None)
    prev_pose = rig_obj.data.pose_position if rig_obj else None
    if rig_obj:
        rig_obj.data.pose_position = "REST"
    bpy.context.view_layer.update()
    corners = [head.matrix_world @ mathutils.Vector(c) for c in head.bound_box]
    mn = mathutils.Vector([min(c[i] for c in corners) for i in range(3)])
    mx = mathutils.Vector([max(c[i] for c in corners) for i in range(3)])
    width = mx.x - mn.x
    size = size or 1.0 * width
    offset = offset if offset is not None else 0.006 * width  # clears the head's facet ridges (no specks)
    atlas_info = face_atlas_build(faces_dir=faces_dir, cell=cell, name=f"{name}_Atlas")
    atlas = bpy.data.images[atlas_info["image"]]
    cols, rows = atlas_info["grid"]
    n = atlas_info["max_index"]
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_grid(bm, x_segments=subdivisions, y_segments=subdivisions, size=size / 2, calc_uvs=True)
    bm.to_mesh(me)
    bm.free()
    decal = bpy.data.objects.new(name, me)
    for c in head.users_collection:
        c.objects.link(decal)
    # face looks along -Y (Blender front view); eye line at `height` of the head
    # the face canvas centre (y=512) sits ~at the eye line (516) -> centre the plane on the eye line
    decal.rotation_euler = (math.radians(90), 0, 0)
    decal.location = ((mn.x + mx.x) / 2, mn.y - 0.25 * size, mn.z + height * (mx.z - mn.z))
    sw = decal.modifiers.new("Conform", "SHRINKWRAP")
    sw.target = head
    sw.wrap_method = "PROJECT"
    sw.use_project_z = True
    sw.use_negative_direction = True
    sw.use_positive_direction = True
    sw.wrap_mode = "ABOVE_SURFACE"
    sw.offset = offset
    bpy.context.view_layer.update()
    # if the head is deformed by an armature (squash & stretch, body rig), the decal must ride the same rig:
    # copy each decal vertex's weights from the nearest head vertex and put an Armature modifier first
    rig = next((m.object for m in head.modifiers if m.type == "ARMATURE" and m.object), None) if follow_rig else None
    if rig is not None:
        from mathutils.kdtree import KDTree
        hv = head.data.vertices
        kd = KDTree(len(hv))
        for v in hv:
            kd.insert(head.matrix_world @ v.co, v.index)
        kd.balance()
        gname = {g.index: g.name for g in head.vertex_groups}
        for v in me.vertices:
            _, i, _ = kd.find(decal.matrix_world @ v.co)
            for g in hv[i].groups:
                n_ = gname.get(g.group)
                if n_ and g.weight > 1e-4:
                    vg = decal.vertex_groups.get(n_) or decal.vertex_groups.new(name=n_)
                    vg.add([v.index], g.weight, "REPLACE")
        am = decal.modifiers.new("Rig", "ARMATURE")
        am.object = rig
        decal.modifiers.move(len(decal.modifiers) - 1, 0)
        # the rig rotates the face (twirls, tilts) but Project uses the decal object's fixed axis -> the face
        # would be pushed off sideways; the armature already places it, so snap to the nearest surface instead
        sw.wrap_method = "NEAREST_SURFACEPOINT"
    decal.parent = head
    decal.matrix_parent_inverse = head.matrix_world.inverted()
    if rig_obj:
        rig_obj.data.pose_position = prev_pose
    decal["expression"] = 1
    ui = decal.id_properties_ui("expression")
    ui.update(min=1, max=n, soft_min=1, soft_max=n, description="Face index (see faces.json for names)")
    decal["faces_dir"] = d
    mat = _face_material(f"{name}_Mat", atlas, cols, rows, n, feature_color, emission)
    me.materials.append(mat)
    decal.visible_shadow = False
    idx = _resolve(expression, catalog, n) if expression is not None else 1
    decal["expression"] = idx
    return {"decal": decal.name, "head": head.name, "material": mat.name, "atlas": atlas.name,
            "grid": [cols, rows], "faces": atlas_info["faces"], "expression": idx,
            "expression_name": catalog.get(idx, {}).get("name"), "follows_rig": rig.name if rig else None, "size": r(size), "location": [r(v) for v in decal.location],
            "names": {k: v.get("name") for k, v in catalog.items()} if len(catalog) <= 60 else None}


def _decal(decal):
    o = get_obj(decal)
    if "expression" not in o:
        raise ValueError(f"{decal} has no 'expression' property - run face_expression_setup first")
    d = o.get("faces_dir") or ASSET_DIR
    return o, _catalog(d), len(_face_files(d)) if os.path.isdir(d) else 50


def _key(o, frame, idx):
    o["expression"] = int(idx)
    o.keyframe_insert('["expression"]', frame=frame)


def _constant(o):
    ad = o.animation_data
    if not ad or not ad.action:
        return
    fcs = []
    if hasattr(ad.action, "fcurves"):
        fcs = list(ad.action.fcurves)
    if not fcs and hasattr(ad.action, "layers"):  # Blender 5 layered actions
        for layer in ad.action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    fcs += list(cb.fcurves)
    for fc in fcs:
        if fc.data_path == '["expression"]':
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"


@recipe("rigging", tags=("face", "expression", "keyframe"))
def face_expression_set(decal="FaceDecal", expression="happy", frame=None) -> dict:
    """Set the face on a decal from face_expression_setup by name ("happy", "angry", "uwu", "star_eyes"...),
    by tag ("blink", "talk", "tears") or by index. frame=N also inserts a CONSTANT keyframe there."""
    o, catalog, n = _decal(decal)
    idx = _resolve(expression, catalog, n)
    if frame is None:
        o["expression"] = idx
    else:
        _key(o, frame, idx)
        _constant(o)
    return {"decal": o.name, "expression": idx, "name": catalog.get(idx, {}).get("name"), "frame": frame}


@recipe("rigging", tags=("face", "expression", "animation", "blink", "keyframe"))
def face_expression_animate(decal="FaceDecal", keys=((1, "happy"),), blink=True, blink_every=72, blink_jitter=24,
                            blink_frames=4, end=None, seed=0) -> dict:
    """Animate the face: keys = [[frame, expression], ...] (name, tag or index), held with CONSTANT
    interpolation. blink=True adds blinks every ~blink_every frames (+/- jitter) for blink_frames frames,
    using each face's "blink" partner from faces.json (happy->content, joy->laughing, neutral->eyes_closed);
    faces without a partner are not blinked, and no blink is placed within 6 frames of an expression change.
    end: last frame (default scene end). Returns the keys and blink frames written."""
    o, catalog, n = _decal(decal)
    sc = bpy.context.scene
    end = int(end or sc.frame_end)
    plan = sorted((int(f), _resolve(e, catalog, n)) for f, e in keys)
    for f, idx in plan:
        _key(o, f, idx)
    blinks = []
    if blink:
        rng = random.Random(seed)
        f = plan[0][0] + blink_every // 2
        changes = [p[0] for p in plan]
        while f + blink_frames < end:
            cur = [i for fr, i in plan if fr <= f]
            base = cur[-1] if cur else plan[0][1]
            partner = catalog.get(base, {}).get("blink")
            near = any(abs(f - c) < 6 or abs(f + blink_frames - c) < 6 for c in changes)
            if partner and not near:
                _key(o, f, partner)
                _key(o, f + blink_frames, base)
                blinks.append(f)
            f += blink_every + rng.randint(-blink_jitter, blink_jitter)
    _constant(o)
    o["expression"] = plan[0][1]
    return {"decal": o.name, "keys": [[f, i, catalog.get(i, {}).get("name")] for f, i in plan], "blinks": blinks,
            "end": end}


@recipe("rigging", tags=("face", "expression", "import", "slice", "ai image", "sprite sheet"))
def face_sheet_import(sheet_path="", out_dir=None, start_index=26, rows=5, cols=5, inset=6, threshold=0.82,
                      eye_width=205, eye_y=516) -> dict:
    """Slice a generated grid sheet of new faces (black ink on white, one face per cell, e.g. from GPT image
    generation using the bundled sheet as a style reference) into Face_<start_index>.. PNGs in the shared
    layout: dark ink -> opaque black, white -> transparent (eye highlights and teeth become holes), then ONE
    scale/offset for the whole sheet so the median left-eye width = eye_width px and the eye row lands at
    eye_y on the 1024 canvas; each face is centred horizontally. out_dir default: the bundled faces folder.
    Add names for the new indices to faces.json afterwards."""
    src = _load_rgba(bpy.path.abspath(sheet_path))[::-1]  # top row first
    lum = src[..., :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    H, W = lum.shape
    cells = []
    for rr in range(rows):
        for c in range(cols):
            y0, y1 = round(rr * H / rows) + inset, round((rr + 1) * H / rows) - inset
            x0, x1 = round(c * W / cols) + inset, round((c + 1) * W / cols) - inset
            a = np.clip((threshold - lum[y0:y1, x0:x1]) / 0.55, 0, 1)
            cells.append(a)
    stats = []
    for a in cells:
        ys, xs = np.nonzero(a > 0.5)
        if not len(xs):
            stats.append(None); continue
        x0, x1 = xs.min(), xs.max()
        left = a[:, x0:x0 + int(0.3 * (x1 - x0)) + 1] > 0.5
        ey, ex = np.nonzero(left)
        stats.append(((x0 + x1) / 2, ex.max() - ex.min() + 1 if len(ex) else None,
                      (ey.min() + ey.max()) / 2 if len(ey) else None))
    ok = [s for s in stats if s and s[1]]
    if not ok:
        raise ValueError("no faces found in the sheet - check rows/cols/threshold")
    S = eye_width / float(np.median([s[1] for s in ok]))
    e_y = float(np.median([s[2] for s in ok]))
    out_dir = bpy.path.abspath(out_dir) if out_dir else ASSET_DIR
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for i, (a, s) in enumerate(zip(cells, stats)):
        if s is None:
            continue
        h, w = a.shape
        rgba = np.zeros((h, w, 4), dtype=np.float32)
        rgba[..., 3] = a
        big = _resize(rgba[::-1], round(w * S), round(h * S))[::-1]  # top row first again
        canvas = np.zeros((1024, 1024, 4), dtype=np.float32)
        ox, oy = round(512 - s[0] * S), round(eye_y - e_y * S)
        bh, bw = big.shape[:2]
        sx0, sy0 = max(0, -ox), max(0, -oy)
        dx0, dy0 = max(0, ox), max(0, oy)
        cw, ch = min(bw - sx0, 1024 - dx0), min(bh - sy0, 1024 - dy0)
        canvas[dy0:dy0 + ch, dx0:dx0 + cw] = big[sy0:sy0 + ch, sx0:sx0 + cw]
        canvas[..., :3] = 0.0
        path = os.path.join(out_dir, f"Face_{start_index + i}.png")
        _save_png(canvas[::-1], path)
        written.append(os.path.basename(path))
    return {"written": written, "count": len(written), "scale": r(S, 3), "out_dir": out_dir,
            "hint": "add {name, tags, blink} entries for the new indices to faces.json"}
