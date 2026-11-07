"""Shading recipes: materials, lighting/world, camera, render settings, compositing, baking.

Conventions
- `target` / `targets`: object name(s). Material recipes with target=None just create the material.
- `demo=True` (where offered) first builds a small sample scene so the recipe can be previewed/tested
  on an empty file.
- Every "do X and return" recipe (render_still, bake_*) restores the engine/samples/selection it touched.
"""
import math
import os
import re
import tempfile

import bmesh
import bpy
from mathutils import Vector

from . import recipe
from ..util import get_obj, r, world_bbox, eevee_engine_id

MESHLIKE = {"MESH", "CURVE", "SURFACE", "FONT", "META"}


# =============================================================== helpers

def _scene():
    return bpy.context.scene


def _link(obj, collection=None):
    (collection or _scene().collection).objects.link(obj)
    return obj


def _demo_mesh(name, kind="sphere", size=2.0, location=(0, 0, 0), segments=32):
    """Mesh object with a UV map, created via bmesh (no operators)."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    if kind == "cube":
        bmesh.ops.create_cube(bm, size=size, calc_uvs=True)
    elif kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size / 2, calc_uvs=True)
    else:
        bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=max(3, segments // 2),
                                  radius=size / 2, calc_uvs=True)
    bm.to_mesh(me)
    bm.free()
    if kind == "sphere":
        for p in me.polygons:
            p.use_smooth = True
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    return _link(obj)


def _objs(targets):
    if targets is None:
        return []
    if isinstance(targets, str):
        targets = [targets]
    return [get_obj(t) for t in targets]


def _scene_meshes(exclude=()):
    bpy.context.view_layer.update()
    return [o for o in bpy.context.view_layer.objects
            if o.type in MESHLIKE and o.visible_get() and o.name not in exclude]


def _bounds(objs):
    """(center, size_vec, radius, corners) for objects; unit cube at origin when nothing."""
    dg = bpy.context.evaluated_depsgraph_get()
    mins, maxs = [], []
    for o in objs:
        bb = world_bbox(o, dg)
        if bb:
            mins.append(bb[0])
            maxs.append(bb[1])
    if not mins:
        mn, mx = Vector((-1, -1, -1)), Vector((1, 1, 1))
    else:
        mn = Vector((min(v.x for v in mins), min(v.y for v in mins), min(v.z for v in mins)))
        mx = Vector((max(v.x for v in maxs), max(v.y for v in maxs), max(v.z for v in maxs)))
    c = (mn + mx) / 2
    size = mx - mn
    corners = [Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]
    return c, size, max(size.length / 2, 1e-3), corners


def _rgba(c, default=(0.8, 0.8, 0.8)):
    c = default if c is None else c
    if isinstance(c, str) and c.startswith("#"):
        h = c.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
    c = list(c)
    if len(c) not in (3, 4):
        raise ValueError(f"color must be (r,g,b), (r,g,b,a) or '#rrggbb', got {c!r}")
    return tuple(c[:3]) + ((c[3],) if len(c) == 4 else (1.0,))


def _new_material(name):
    """Create material (or rebuild an existing one in place, keeping its assignments)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if getattr(mat, "use_nodes", True) is False:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    return mat, nt, out


def _node(nt, ntype, loc=(0, 0), inputs=None, **props):
    n = nt.nodes.new(ntype)
    n.location = loc
    for k, v in props.items():
        setattr(n, k, v)
    for k, v in (inputs or {}).items():
        n.inputs[k].default_value = v
    return n


def _sock(sockets, ident):
    for s in sockets:
        if s.identifier == ident:
            return s
    raise KeyError(ident)


def _mix_rgb(nt, blend="MIX", loc=(0, 0), fac=1.0):
    m = nt.nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    m.blend_type = blend
    m.location = loc
    _sock(m.inputs, "Factor_Float").default_value = fac
    return m, _sock(m.inputs, "A_Color"), _sock(m.inputs, "B_Color"), _sock(m.outputs, "Result_Color")


def _ramp(nt, stops, loc=(0, 0), interpolation="LINEAR"):
    """stops: [(pos, rgba)]"""
    n = nt.nodes.new("ShaderNodeValToRGB")
    n.location = loc
    cr = n.color_ramp
    cr.interpolation = interpolation
    while len(cr.elements) > 1:
        cr.elements.remove(cr.elements[-1])
    cr.elements[0].position = stops[0][0]
    cr.elements[0].color = _rgba(stops[0][1])
    for pos, col in stops[1:]:
        e = cr.elements.new(pos)
        e.color = _rgba(col)
    return n


def _assign(mat, objs, all_slots=False):
    done = []
    for o in objs:
        if o.type not in MESHLIKE:
            raise ValueError(f"{o.name} is a {o.type}; materials need a mesh/curve/text object")
        if not o.material_slots:
            o.data.materials.append(mat)
        elif all_slots:
            for s in o.material_slots:
                s.material = mat
        else:
            o.material_slots[o.active_material_index].material = mat
        if any(m.type == "NODES" for m in o.modifiers):
            # Geometry Nodes that generate new geometry (Grid, Cube...) drop the mesh's material slots; an
            # object-linked slot overrides the evaluated data, so the material survives GN evaluation.
            for s in (o.material_slots if all_slots else [o.material_slots[o.active_material_index]]):
                s.link = "OBJECT"
                s.material = mat
        done.append(o.name)
    return done


def _mat_result(mat, assigned=(), **extra):
    nt = mat.node_tree
    out = {"material": mat.name, "nodes": len(nt.nodes), "links": len(nt.links),
           "assigned_to": list(assigned)}
    out.update(extra)
    return out


def _look_at_quat(direction):
    return Vector(direction).to_track_quat("-Z", "Y")


def _ensure_world(name):
    scene = _scene()
    world = bpy.data.worlds.get(name) or bpy.data.worlds.new(name)
    if getattr(world, "use_nodes", True) is False:
        world.use_nodes = True
    scene.world = world
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (500, 0)
    return world, nt, out


def _fit_camera(cam_obj, points, margin=0.08):
    """Dolly cam_obj (keeping its rotation) so all world points fit the frame. Returns distance."""
    scene = _scene()
    cam = cam_obj.data
    rot = cam_obj.matrix_world.to_quaternion()
    right, up, fwd = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0)), rot @ Vector((0, 0, -1))
    center = sum(points, Vector()) / len(points)
    loc = [((p - center).dot(right) * (1 + margin), (p - center).dot(up) * (1 + margin), (p - center).dot(fwd))
           for p in points]
    rx, ry = scene.render.resolution_x * scene.render.pixel_aspect_x, scene.render.resolution_y * scene.render.pixel_aspect_y
    if cam.type == "ORTHO":
        need = max(max(abs(x) for x, _, _ in loc) * 2, max(abs(y) for _, y, _ in loc) * 2 * rx / ry) if rx >= ry \
            else max(max(abs(y) for _, y, _ in loc) * 2, max(abs(x) for x, _, _ in loc) * 2 * ry / rx)
        cam.ortho_scale = max(need, 1e-3)
        depth = max(abs(z) for _, _, z in loc)
        dist = depth + max(need, 1.0)
    else:
        sensor = cam.sensor_width if cam.sensor_fit in {"AUTO", "HORIZONTAL"} else cam.sensor_height
        t = sensor / 2 / cam.lens
        if cam.sensor_fit == "VERTICAL" or (cam.sensor_fit == "AUTO" and ry > rx):
            ty, tx = t, t * rx / ry
        else:
            tx, ty = t, t * ry / rx
        dist = max(max(abs(x) / tx - z, abs(y) / ty - z) for x, y, z in loc)
    cam_obj.location = center - fwd * dist
    span = max((p - center).length for p in points)
    cam.clip_start = max(min(0.1, dist * 0.01), 1e-3)
    cam.clip_end = max(cam.clip_end, (dist + span) * 2)
    return dist


def _make_camera(name, lens=50.0, ortho=False):
    cam_obj = bpy.data.objects.get(name)
    if cam_obj is None:
        cam_obj = _link(bpy.data.objects.new(name, bpy.data.cameras.new(name)))
    elif cam_obj.type != "CAMERA":
        raise ValueError(f"{name!r} exists but is a {cam_obj.type}, not a camera")
    cam_obj.data.lens = lens
    cam_obj.data.type = "ORTHO" if ortho else "PERSP"
    return cam_obj


def _set_linear(id_obj):
    ad = id_obj.animation_data
    if not ad or not ad.action:
        return
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        fcs = list(cb.fcurves) if cb else []
    except Exception:
        fcs = list(getattr(ad.action, "fcurves", []))
    for fc in fcs:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


def _engine_id(engine):
    e = (engine or "").upper()
    if e in {"EEVEE", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}:
        return eevee_engine_id()
    if e in {"CYCLES"}:
        return "CYCLES"
    if e in {"WORKBENCH", "BLENDER_WORKBENCH"}:
        return "BLENDER_WORKBENCH"
    raise ValueError(f"engine must be EEVEE, CYCLES or WORKBENCH, got {engine!r}")


def _set_samples(scene, samples):
    if samples is None:
        return
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = int(samples)
    elif scene.render.engine.startswith("BLENDER_EEVEE"):
        scene.eevee.taa_render_samples = int(samples)


def _snapshot_render(scene):
    s = {"engine": scene.render.engine, "rx": scene.render.resolution_x, "ry": scene.render.resolution_y,
         "pct": scene.render.resolution_percentage, "filepath": scene.render.filepath,
         "fmt": scene.render.image_settings.file_format, "camera": scene.camera,
         "frame": scene.frame_current, "transparent": scene.render.film_transparent,
         "cyc_samples": scene.cycles.samples, "eevee_samples": scene.eevee.taa_render_samples}
    return s


def _restore_render(scene, s):
    scene.render.engine = s["engine"]
    scene.render.resolution_x, scene.render.resolution_y = s["rx"], s["ry"]
    scene.render.resolution_percentage = s["pct"]
    scene.render.filepath = s["filepath"]
    try:
        scene.render.image_settings.file_format = s["fmt"]
    except Exception:
        pass
    scene.camera = s["camera"]
    scene.render.film_transparent = s["transparent"]
    scene.cycles.samples = s["cyc_samples"]
    scene.eevee.taa_render_samples = s["eevee_samples"]
    if scene.frame_current != s["frame"]:
        scene.frame_set(s["frame"])


def _image_size_on_disk(path):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        return list(img.size)
    finally:
        bpy.data.images.remove(img)


def _out_dir(output_dir, sub="blendpro_out"):
    if output_dir:
        d = bpy.path.abspath(output_dir)
    elif bpy.data.filepath:
        d = os.path.join(os.path.dirname(bpy.data.filepath), sub)
    else:
        d = os.path.join(tempfile.gettempdir(), sub)
    os.makedirs(d, exist_ok=True)
    return d


# =============================================================== MATERIALS

_PBR_KEYS = [
    ("normal", {"normal", "nor", "nrm", "norm", "normalgl", "normaldx", "normalmap", "nrml"}),
    ("roughness", {"roughness", "rough", "rgh"}),
    ("glossiness", {"gloss", "glossiness"}),
    ("metallic", {"metallic", "metalness", "metal", "mtl"}),
    ("ao", {"ao", "ambientocclusion", "occlusion", "ambient"}),
    ("height", {"height", "disp", "displacement", "bump", "depth"}),
    ("opacity", {"opacity", "alpha", "transparency", "cutout"}),
    ("emission", {"emission", "emissive", "emit", "glow"}),
    ("basecolor", {"basecolor", "albedo", "diffuse", "diff", "col", "color", "colour", "base"}),
]
_IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga", ".bmp", ".webp", ".hdr"}


def _classify_tex(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"([a-z])([A-Z])", r"\1_\2", stem).lower()
    toks = [t for t in re.split(r"[^a-z0-9]+", stem) if t]
    joined = set(toks) | {a + b for a, b in zip(toks, toks[1:])}
    for kind, keys in _PBR_KEYS:
        if joined & keys:
            dx = bool(joined & {"dx", "directx", "normaldx"})
            return kind, dx
    return None, False


@recipe("shading", tags=("material", "pbr", "textures", "import"))
def pbr_material_from_textures(folder=None, files=None, name="PBR_Material", target=None, name_filter=None,
                               uv_scale=1.0, normal_strength=1.0, displacement_scale=0.05,
                               true_displacement=False, ao_strength=1.0) -> dict:
    '''Build a Principled PBR material from a texture set (folder or file list), auto-detecting maps by filename.
    Detects basecolor/albedo, normal (GL; DX green flipped), roughness (or gloss, inverted), metallic, ao (multiplied),
    height (Displacement node, bump by default), opacity, emission; sets sRGB vs Non-Color correctly.
    files may be a list of paths or {kind: path}. name_filter keeps only filenames containing it. target=None: material only.'''
    paths = {}
    ignored = []
    if isinstance(files, dict):
        for k, p in files.items():
            k = k.lower()
            if k not in {kd for kd, _ in _PBR_KEYS}:
                raise ValueError(f"unknown map kind {k!r}; use one of {[kd for kd, _ in _PBR_KEYS]}")
            paths[k] = (bpy.path.abspath(p), "dx" in os.path.basename(p).lower())
    else:
        cand = []
        if folder:
            d = bpy.path.abspath(folder)
            if not os.path.isdir(d):
                raise ValueError(f"folder not found: {d}")
            cand += [os.path.join(d, f) for f in sorted(os.listdir(d))]
        cand += [bpy.path.abspath(p) for p in (files or [])]
        cand = [p for p in cand if os.path.splitext(p)[1].lower() in _IMG_EXT]
        if name_filter:
            cand = [p for p in cand if name_filter.lower() in os.path.basename(p).lower()]
        if not cand:
            raise ValueError("no image files found (give folder= or files=[...]; supported: "
                             f"{sorted(_IMG_EXT)})")
        for p in cand:
            kind, dx = _classify_tex(p)
            if kind is None:
                ignored.append(os.path.basename(p))
                continue
            if kind in paths:
                # prefer GL normals and non-preview names
                if kind == "normal" and paths[kind][1] and not dx:
                    ignored.append(os.path.basename(paths[kind][0]))
                    paths[kind] = (p, dx)
                else:
                    ignored.append(os.path.basename(p))
                continue
            paths[kind] = (p, dx)
    if "roughness" in paths and "glossiness" in paths:
        ignored.append(os.path.basename(paths.pop("glossiness")[0]))
    if not paths:
        raise ValueError(f"no PBR maps recognised among: {ignored}. Name files like *_basecolor/_normal/_roughness, "
                         "or pass files={'basecolor': path, ...}")
    for k, (p, _) in paths.items():
        if not os.path.isfile(p):
            raise ValueError(f"{k} texture not found: {p}")

    mat, nt, out = _new_material(name)
    bsdf = _node(nt, "ShaderNodeBsdfPrincipled", (250, 0))
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    tc = _node(nt, "ShaderNodeTexCoord", (-1100, 0))
    mp = _node(nt, "ShaderNodeMapping", (-900, 0))
    mp.inputs["Scale"].default_value = (uv_scale, uv_scale, uv_scale)
    nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
    order = ["basecolor", "ao", "metallic", "roughness", "glossiness", "normal", "height", "opacity", "emission"]
    texn = {}
    y = 400
    for k in order:
        if k not in paths:
            continue
        img = bpy.data.images.load(paths[k][0], check_existing=True)
        img.colorspace_settings.name = "sRGB" if k in {"basecolor", "emission"} else "Non-Color"
        t = _node(nt, "ShaderNodeTexImage", (-600, y), image=img)
        t.label = k
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
        texn[k] = t
        y -= 280
    if "basecolor" in texn:
        col = texn["basecolor"].outputs["Color"]
        if "ao" in texn:
            m, a, b, res = _mix_rgb(nt, "MULTIPLY", (-150, 450), ao_strength)
            nt.links.new(col, a)
            nt.links.new(texn["ao"].outputs["Color"], b)
            col = res
        nt.links.new(col, bsdf.inputs["Base Color"])
    if "metallic" in texn:
        nt.links.new(texn["metallic"].outputs["Color"], bsdf.inputs["Metallic"])
    if "roughness" in texn:
        nt.links.new(texn["roughness"].outputs["Color"], bsdf.inputs["Roughness"])
    elif "glossiness" in texn:
        inv = _node(nt, "ShaderNodeInvert", (-150, -100))
        nt.links.new(texn["glossiness"].outputs["Color"], inv.inputs["Color"])
        nt.links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])
    if "normal" in texn:
        ncol = texn["normal"].outputs["Color"]
        if paths["normal"][1]:  # DirectX -> flip green
            sep = _node(nt, "ShaderNodeSeparateColor", (-300, -400))
            inv = _node(nt, "ShaderNodeMath", (-150, -400), operation="SUBTRACT")
            inv.inputs[0].default_value = 1.0
            comb = _node(nt, "ShaderNodeCombineColor", (0, -400))
            nt.links.new(ncol, sep.inputs["Color"])
            nt.links.new(sep.outputs["Green"], inv.inputs[1])
            nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
            nt.links.new(inv.outputs["Value"], comb.inputs["Green"])
            nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
            ncol = comb.outputs["Color"]
        nm = _node(nt, "ShaderNodeNormalMap", (50, -250))
        nm.inputs["Strength"].default_value = normal_strength
        nt.links.new(ncol, nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if "height" in texn:
        dn = _node(nt, "ShaderNodeDisplacement", (300, -500))
        dn.inputs["Scale"].default_value = displacement_scale
        dn.inputs["Midlevel"].default_value = 0.5
        nt.links.new(texn["height"].outputs["Color"], dn.inputs["Height"])
        nt.links.new(dn.outputs["Displacement"], out.inputs["Displacement"])
        mat.displacement_method = "BOTH" if true_displacement else "BUMP"
    if "opacity" in texn:
        nt.links.new(texn["opacity"].outputs["Color"], bsdf.inputs["Alpha"])
    if "emission" in texn:
        nt.links.new(texn["emission"].outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0
    assigned = _assign(mat, _objs(target)) if target else []
    return _mat_result(mat, assigned, maps={k: os.path.basename(p) for k, (p, _) in paths.items()},
                       directx_normal=bool(paths.get("normal", (0, False))[1]), ignored=ignored[:12])


_PROC_PRESETS = ("brushed_metal", "polished_metal", "wood", "concrete", "car_paint", "plastic", "rubber")


@recipe("shading", tags=("material", "procedural", "metal", "wood", "concrete", "car paint"))
def procedural_material(preset="brushed_metal", name=None, target=None, color=None, scale=1.0,
                        roughness=None) -> dict:
    '''Procedural (texture-free, UV-free) material preset: brushed_metal, polished_metal, wood, concrete, car_paint, plastic, rubber.
    Uses Object coordinates so it works on any mesh without UVs; scale shrinks/enlarges the pattern.
    color tints the preset (e.g. gold (1,0.77,0.34) for polished_metal); roughness overrides the base roughness.'''
    if preset not in _PROC_PRESETS:
        raise ValueError(f"preset must be one of {_PROC_PRESETS}")
    mat, nt, out = _new_material(name or preset.replace("_", " ").title().replace(" ", ""))
    b = _node(nt, "ShaderNodeBsdfPrincipled", (300, 0))
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    tc = _node(nt, "ShaderNodeTexCoord", (-1100, 0))
    mp = _node(nt, "ShaderNodeMapping", (-900, 0))
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]
    L = nt.links.new
    bi = b.inputs

    def bump(height_socket, strength, distance=0.02, loc=(0, -500)):
        bp = _node(nt, "ShaderNodeBump", loc)
        bp.inputs["Strength"].default_value = strength
        bp.inputs["Distance"].default_value = distance
        L(height_socket, bp.inputs["Height"])
        L(bp.outputs["Normal"], bi["Normal"])
        return bp

    if preset == "brushed_metal":
        mp.inputs["Scale"].default_value = (scale, scale * 150, scale)
        nz = _node(nt, "ShaderNodeTexNoise", (-650, 0), inputs={"Scale": 8.0, "Detail": 8.0})
        L(vec, nz.inputs["Vector"])
        rmap = _node(nt, "ShaderNodeMapRange", (-350, -100))
        rmap.inputs["From Min"].default_value, rmap.inputs["From Max"].default_value = 0.3, 0.7
        base_r = 0.25 if roughness is None else roughness
        rmap.inputs["To Min"].default_value, rmap.inputs["To Max"].default_value = base_r * 0.6, base_r * 1.4
        L(nz.outputs["Fac"], rmap.inputs["Value"])
        L(rmap.outputs["Result"], bi["Roughness"])
        bi["Base Color"].default_value = _rgba(color, (0.75, 0.75, 0.77))
        bi["Metallic"].default_value = 1.0
        bi["Anisotropic"].default_value = 0.6
    elif preset == "polished_metal":
        nz = _node(nt, "ShaderNodeTexNoise", (-650, 0), inputs={"Scale": 3.0, "Detail": 4.0})
        L(vec, nz.inputs["Vector"])
        rmap = _node(nt, "ShaderNodeMapRange", (-350, -100))
        base_r = 0.08 if roughness is None else roughness
        rmap.inputs["To Min"].default_value, rmap.inputs["To Max"].default_value = base_r * 0.5, base_r * 1.6
        L(nz.outputs["Fac"], rmap.inputs["Value"])
        L(rmap.outputs["Result"], bi["Roughness"])
        bi["Base Color"].default_value = _rgba(color, (0.92, 0.92, 0.94))
        bi["Metallic"].default_value = 1.0
    elif preset == "wood":
        light = _rgba(color, (0.45, 0.22, 0.08))
        dark = tuple(c * 0.3 for c in light[:3]) + (1.0,)
        wv = _node(nt, "ShaderNodeTexWave", (-650, 0), wave_type="RINGS", rings_direction="X",
                   inputs={"Scale": 3.0, "Distortion": 6.0, "Detail": 3.0, "Detail Scale": 1.5})
        L(vec, wv.inputs["Vector"])
        rp = _ramp(nt, [(0.0, dark), (0.7, light)], (-350, 100))
        L(wv.outputs["Fac"], rp.inputs["Fac"])
        L(rp.outputs["Color"], bi["Base Color"])
        bi["Roughness"].default_value = 0.55 if roughness is None else roughness
        bump(wv.outputs["Fac"], 0.15)
    elif preset == "concrete":
        nz = _node(nt, "ShaderNodeTexNoise", (-650, 100), inputs={"Scale": 6.0, "Detail": 12.0, "Roughness": 0.6})
        L(vec, nz.inputs["Vector"])
        base = _rgba(color, (0.42, 0.41, 0.39))
        rp = _ramp(nt, [(0.35, tuple(c * 0.6 for c in base[:3])), (0.65, base)], (-350, 150))
        L(nz.outputs["Fac"], rp.inputs["Fac"])
        L(rp.outputs["Color"], bi["Base Color"])
        vo = _node(nt, "ShaderNodeTexVoronoi", (-650, -250), inputs={"Scale": 40.0})
        L(vec, vo.inputs["Vector"])
        add = _node(nt, "ShaderNodeMath", (-350, -250), operation="ADD")
        L(nz.outputs["Fac"], add.inputs[0])
        L(vo.outputs["Distance"], add.inputs[1])
        bi["Roughness"].default_value = 0.9 if roughness is None else roughness
        bump(add.outputs["Value"], 0.3)
    elif preset == "car_paint":
        bi["Base Color"].default_value = _rgba(color, (0.6, 0.02, 0.02))
        bi["Metallic"].default_value = 0.6
        bi["Roughness"].default_value = 0.35 if roughness is None else roughness
        bi["Coat Weight"].default_value = 1.0
        bi["Coat Roughness"].default_value = 0.03
        vo = _node(nt, "ShaderNodeTexVoronoi", (-650, -250), inputs={"Scale": 800.0})
        L(vec, vo.inputs["Vector"])
        bump(vo.outputs["Distance"], 0.05, 0.005)  # metallic flakes under a smooth clear coat
    elif preset == "plastic":
        bi["Base Color"].default_value = _rgba(color, (0.05, 0.25, 0.8))
        nz = _node(nt, "ShaderNodeTexNoise", (-650, 0), inputs={"Scale": 20.0})
        L(vec, nz.inputs["Vector"])
        rmap = _node(nt, "ShaderNodeMapRange", (-350, -100))
        base_r = 0.35 if roughness is None else roughness
        rmap.inputs["To Min"].default_value, rmap.inputs["To Max"].default_value = base_r * 0.85, base_r * 1.15
        L(nz.outputs["Fac"], rmap.inputs["Value"])
        L(rmap.outputs["Result"], bi["Roughness"])
    elif preset == "rubber":
        bi["Base Color"].default_value = _rgba(color, (0.02, 0.02, 0.02))
        bi["Roughness"].default_value = 0.85 if roughness is None else roughness
        bi["Sheen Weight"].default_value = 0.15
        nz = _node(nt, "ShaderNodeTexNoise", (-650, -250), inputs={"Scale": 300.0})
        L(vec, nz.inputs["Vector"])
        bump(nz.outputs["Fac"], 0.08, 0.005)
    assigned = _assign(mat, _objs(target)) if target else []
    return _mat_result(mat, assigned, preset=preset)


@recipe("shading", tags=("material", "glass", "transmission", "eevee", "cycles"))
def glass_material(name="Glass", target=None, color=(1, 1, 1), roughness=0.0, ior=1.45, thin_wall=False,
                   absorption_density=0.0) -> dict:
    '''Physically based glass that renders correctly in both EEVEE and Cycles.
    thin_wall=True for single-sided/hollow panes (windows, bottles modelled as one surface): uses Principled Thin Wall
    and EEVEE slab thickness. absorption_density>0 adds tinted volume absorption for thick solid glass (Cycles).
    Sets EEVEE raytraced refraction on the material; also enable Raytracing in render settings for best EEVEE results.'''
    if ior < 1.0:
        raise ValueError("ior must be >= 1.0")
    mat, nt, out = _new_material(name)
    b = _node(nt, "ShaderNodeBsdfPrincipled", (250, 0))
    col = _rgba(color)
    b.inputs["Base Color"].default_value = col
    b.inputs["Transmission Weight"].default_value = 1.0
    b.inputs["Roughness"].default_value = roughness
    b.inputs["IOR"].default_value = ior
    if "Thin Wall" in b.inputs:
        b.inputs["Thin Wall"].default_value = bool(thin_wall)
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    if absorption_density > 0:
        va = _node(nt, "ShaderNodeVolumeAbsorption", (250, -350))
        va.inputs["Color"].default_value = col
        va.inputs["Density"].default_value = absorption_density
        nt.links.new(va.outputs["Volume"], out.inputs["Volume"])
    for attr, val in (("use_raytrace_refraction", True), ("thickness_mode", "SLAB" if thin_wall else "SPHERE"),
                      ("surface_render_method", "DITHERED"), ("use_backface_culling", False)):
        if hasattr(mat, attr):
            setattr(mat, attr, val)
    assigned = _assign(mat, _objs(target)) if target else []
    return _mat_result(mat, assigned, thin_wall=bool(thin_wall), ior=ior,
                       thickness_mode=getattr(mat, "thickness_mode", None))


@recipe("shading", tags=("material", "emission", "neon", "glow"))
def emissive_neon_material(name="Neon", target=None, color=(0.1, 0.6, 1.0), strength=15.0, white_core=0.5) -> dict:
    '''Neon-tube emission: saturated colour at grazing angles, hot white-ish core facing the camera.
    strength is emission strength (W/m2-ish; 5-50 typical). white_core 0..1 controls how much of the facing area is
    whitened. For visible bloom add compositor_effects(glare_type="Fog Glow"). Lights the scene in Cycles and EEVEE.'''
    if strength < 0:
        raise ValueError("strength must be >= 0")
    mat, nt, out = _new_material(name)
    c = _rgba(color)
    core = tuple(min(1.0, x * 0.4 + 0.6) for x in c[:3]) + (1.0,)
    lw = _node(nt, "ShaderNodeLayerWeight", (-500, 0), inputs={"Blend": 0.5})
    rp = _ramp(nt, [(0.0, core), (max(0.01, min(0.99, white_core)), c)], (-250, 0))
    em = _node(nt, "ShaderNodeEmission", (200, 0))
    em.inputs["Strength"].default_value = strength
    nt.links.new(lw.outputs["Facing"], rp.inputs["Fac"])
    nt.links.new(rp.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    assigned = _assign(mat, _objs(target)) if target else []
    return _mat_result(mat, assigned, strength=strength)


@recipe("shading", tags=("material", "toon", "cel", "npr", "eevee", "rim", "fresnel"))
def toon_shader_material(name="Toon", target=None, color=(0.9, 0.35, 0.2), shadow_color=None, steps=2,
                         rim=True, rim_color=(1, 1, 1), rim_width=0.15, rim_strength=0.8) -> dict:
    '''Cel/toon shader: Diffuse -> Shader to RGB -> constant ColorRamp bands (+ optional fresnel rim light).
    steps = number of light bands (2-5). shadow_color defaults to a darker colour. EEVEE ONLY: Shader to RGB is not
    supported by Cycles. Pair with outline_effect for ink lines.'''
    steps = int(steps)
    if not 2 <= steps <= 8:
        raise ValueError("steps must be 2..8")
    mat, nt, out = _new_material(name)
    c = _rgba(color)
    sc = _rgba(shadow_color) if shadow_color is not None else tuple(x * 0.35 for x in c[:3]) + (1.0,)
    diff = _node(nt, "ShaderNodeBsdfDiffuse", (-900, 0))
    s2r = _node(nt, "ShaderNodeShaderToRGB", (-700, 0))
    stops = []
    for i in range(steps):
        t = i / (steps - 1)
        stops.append((i / steps if i else 0.0, tuple(sc[k] + (c[k] - sc[k]) * t for k in range(3))))
    rp = _ramp(nt, stops, (-500, 0), "CONSTANT")
    nt.links.new(diff.outputs["BSDF"], s2r.inputs["Shader"])
    nt.links.new(s2r.outputs["Color"], rp.inputs["Fac"])
    col = rp.outputs["Color"]
    if rim:
        lw = _node(nt, "ShaderNodeLayerWeight", (-700, -300), inputs={"Blend": 0.5})
        rr = _ramp(nt, [(0.0, (0, 0, 0)), (max(0.01, 1 - rim_width), (1, 1, 1))], (-500, -300), "CONSTANT")
        fac = _node(nt, "ShaderNodeMath", (-250, -300), operation="MULTIPLY")
        fac.inputs[1].default_value = rim_strength
        m, a, bb, res = _mix_rgb(nt, "ADD", (-50, 0))
        nt.links.new(lw.outputs["Facing"], rr.inputs["Fac"])
        nt.links.new(rr.outputs["Color"], fac.inputs[0])
        nt.links.new(fac.outputs["Value"], _sock(m.inputs, "Factor_Float"))
        nt.links.new(col, a)
        bb.default_value = _rgba(rim_color)
        col = res
    em = _node(nt, "ShaderNodeEmission", (250, 0))
    nt.links.new(col, em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    assigned = _assign(mat, _objs(target)) if target else []
    return _mat_result(mat, assigned, steps=steps, rim=bool(rim), engine_note="EEVEE only (Shader to RGB)")


@recipe("shading", tags=("material", "triplanar", "box projection", "no uv"))
def triplanar_material(name="Triplanar", target=None, image_path=None, scale=1.0, blend=0.25,
                       roughness=0.5, coordinates="Object") -> dict:
    '''Box/triplanar-projected image material - textures meshes with no or bad UVs (scans, AI/Tripo meshes, kitbash).
    image_path=None uses a generated colour grid (handy to judge projection). blend 0..1 softens seams between the 3
    projections; scale tiles the texture; coordinates 'Object' (moves with object) or 'Generated'.'''
    if coordinates not in {"Object", "Generated"}:
        raise ValueError("coordinates must be 'Object' or 'Generated'")
    if image_path:
        p = bpy.path.abspath(image_path)
        if not os.path.isfile(p):
            raise ValueError(f"image not found: {p}")
        img = bpy.data.images.load(p, check_existing=True)
    else:
        img = bpy.data.images.get(f"{name}_Grid") or bpy.data.images.new(f"{name}_Grid", 1024, 1024)
        img.generated_type = "COLOR_GRID"
    mat, nt, out = _new_material(name)
    b = _node(nt, "ShaderNodeBsdfPrincipled", (250, 0))
    b.inputs["Roughness"].default_value = roughness
    tc = _node(nt, "ShaderNodeTexCoord", (-800, 0))
    mp = _node(nt, "ShaderNodeMapping", (-600, 0))
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    tx = _node(nt, "ShaderNodeTexImage", (-350, 0), image=img, projection="BOX")
    tx.projection_blend = max(0.0, min(1.0, blend))
    nt.links.new(tc.outputs[coordinates], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], tx.inputs["Vector"])
    nt.links.new(tx.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    assigned = _assign(mat, _objs(target)) if target else []
    return _mat_result(mat, assigned, image=img.name, projection="BOX", blend=tx.projection_blend)


@recipe("shading", tags=("material", "uv", "checker", "debug"))
def uv_checker_material(targets=None, action="apply", grid_type="COLOR_GRID", resolution=1024, uv_scale=1.0,
                        demo=False) -> dict:
    '''Temporarily put a UV checker on objects to inspect stretching/seams, then restore originals.
    action='apply' stores each object's slot materials and overrides ALL slots; action='remove' restores them and
    deletes the checker. targets=None = all visible meshes. grid_type COLOR_GRID|UV_GRID. demo=True adds a sphere first.'''
    if action not in {"apply", "remove"}:
        raise ValueError("action must be 'apply' or 'remove'")
    if grid_type not in {"COLOR_GRID", "UV_GRID"}:
        raise ValueError("grid_type must be COLOR_GRID or UV_GRID")
    if demo:
        _demo_mesh("UVCheckDemo", "sphere")
    objs = _objs(targets) if targets else [o for o in _scene_meshes() if o.type == "MESH"]
    if not objs:
        raise ValueError("no mesh objects to apply the checker to (pass targets or demo=True)")
    key = "bp_uvcheck_prev"
    if action == "remove":
        restored = []
        for o in objs:
            if key not in o:
                continue
            prev = list(o[key])
            if not prev:
                o.data.materials.clear()
            else:
                for i, nm in enumerate(prev):
                    if i < len(o.material_slots):
                        o.material_slots[i].material = bpy.data.materials.get(nm) if nm else None
            del o[key]
            restored.append(o.name)
        mat = bpy.data.materials.get("UV_Checker")
        if mat and mat.users == 0:
            bpy.data.materials.remove(mat)
            img = bpy.data.images.get("UV_Checker_Grid")
            if img and img.users == 0:
                bpy.data.images.remove(img)
        return {"action": "remove", "restored": restored}
    img = bpy.data.images.get("UV_Checker_Grid") or bpy.data.images.new("UV_Checker_Grid", resolution, resolution)
    img.generated_type = grid_type
    mat, nt, out = _new_material("UV_Checker")
    b = _node(nt, "ShaderNodeBsdfPrincipled", (250, 0), inputs={"Roughness": 0.6})
    tc = _node(nt, "ShaderNodeTexCoord", (-800, 0))
    mp = _node(nt, "ShaderNodeMapping", (-600, 0))
    mp.inputs["Scale"].default_value = (uv_scale, uv_scale, 1)
    tx = _node(nt, "ShaderNodeTexImage", (-350, 0), image=img)
    nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], tx.inputs["Vector"])
    nt.links.new(tx.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    applied, no_uv = [], []
    for o in objs:
        if o.type != "MESH":
            continue
        if key not in o:
            o[key] = [s.material.name if s.material else "" for s in o.material_slots]
        _assign(mat, [o], all_slots=True)
        applied.append(o.name)
        if not o.data.uv_layers:
            no_uv.append(o.name)
    return {"action": "apply", "material": mat.name, "applied": applied, "objects_without_uvs": no_uv,
            "hint": "run again with action='remove' to restore original materials"}


_PALETTE = [(0.8, 0.18, 0.15), (0.95, 0.6, 0.1), (0.2, 0.6, 0.35), (0.15, 0.4, 0.8), (0.55, 0.25, 0.7)]


@recipe("shading", tags=("material", "random", "variation", "object info", "palette"))
def random_color_per_object(targets=None, name="RandomPalette", palette=None, roughness=0.5, demo=False) -> dict:
    '''One shared material that gives every object a different colour from a palette (Object Info > Random).
    Great for kitbash/crowds/instancing: one material, per-object variation, survives duplication.
    palette: list of RGB tuples or '#hex' (default 5 colours). targets=None = all visible meshes; demo=True adds 5 cubes.'''
    pal = [_rgba(c) for c in (palette or _PALETTE)]
    if len(pal) < 2:
        raise ValueError("palette needs at least 2 colours")
    if demo:
        for i in range(5):
            _demo_mesh(f"VariantDemo_{i}", "cube", 1.0, (i * 1.5, 0, 0.5))
    objs = _objs(targets) if targets else _scene_meshes()
    if not objs:
        raise ValueError("no objects to assign (pass targets or demo=True)")
    mat, nt, out = _new_material(name)
    oi = _node(nt, "ShaderNodeObjectInfo", (-600, 0))
    n = len(pal)
    rp = _ramp(nt, [(i / n, c) for i, c in enumerate(pal)], (-350, 0), "CONSTANT")
    b = _node(nt, "ShaderNodeBsdfPrincipled", (250, 0), inputs={"Roughness": roughness})
    nt.links.new(oi.outputs["Random"], rp.inputs["Fac"])
    nt.links.new(rp.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    assigned = _assign(mat, objs)
    return _mat_result(mat, assigned, palette_size=n)


@recipe("shading", tags=("material", "vertex color", "color attribute", "scan", "photogrammetry"))
def vertex_color_material(target=None, attribute=None, name="VertexColor", roughness=0.6, demo=False) -> dict:
    '''Material that displays a mesh colour attribute (vertex/corner colours) - for scans, AI-generated and painted meshes.
    attribute=None uses the mesh's active render colour attribute. Errors if the target has no colour attributes.
    target=None just creates the material; demo=True creates a cube with a position-based 'Col' attribute.'''
    if demo and target is None:
        o = _demo_mesh("VertexColorDemo", "cube", 2.0)
        ca = o.data.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
        for i, v in enumerate(o.data.vertices):
            ca.data[i].color = (v.co.x * 0.5 + 0.5, v.co.y * 0.5 + 0.5, v.co.z * 0.5 + 0.5, 1.0)
        o.data.color_attributes.active_color = ca
        o.data.color_attributes.render_color_index = 0
        target = o.name
    layer = attribute or ""
    obj = None
    if target:
        obj = get_obj(target)
        if obj.type != "MESH":
            raise ValueError(f"{obj.name} is not a mesh")
        cas = obj.data.color_attributes
        if not cas:
            raise ValueError(f"{obj.name} has no colour attributes (paint vertex colours or bake one first)")
        names = [c.name for c in cas]
        if attribute and attribute not in names:
            raise ValueError(f"attribute {attribute!r} not on {obj.name}; available: {names}")
        if not attribute:
            idx = cas.render_color_index if 0 <= cas.render_color_index < len(cas) else 0
            layer = cas[idx].name
    mat, nt, out = _new_material(name)
    vc = _node(nt, "ShaderNodeVertexColor", (-350, 0), layer_name=layer)
    b = _node(nt, "ShaderNodeBsdfPrincipled", (250, 0), inputs={"Roughness": roughness})
    nt.links.new(vc.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    assigned = _assign(mat, [obj]) if obj else []
    return _mat_result(mat, assigned, attribute=layer or "(active)")


@recipe("shading", tags=("material", "replace", "swap", "cleanup"))
def replace_material(old="", new="", objects=None, remove_old=False, demo=False) -> dict:
    '''Swap material `old` for `new` in every slot (object- or data-linked) of all objects, or only `objects`.
    remove_old=True deletes `old` if it ends up unused. demo=True first creates materials old/new and 2 cubes using old.'''
    if demo:
        om = bpy.data.materials.get(old) or bpy.data.materials.new(old or "OldMat")
        bpy.data.materials.get(new) or bpy.data.materials.new(new or "NewMat")
        old, new = om.name, new or "NewMat"
        for i in range(2):
            _demo_mesh(f"ReplaceDemo_{i}", "cube", 1.0, (i * 1.5, 0, 0)).data.materials.append(om)
    mats = bpy.data.materials
    if old not in mats:
        import difflib
        raise ValueError(f"material {old!r} not found. Close: {difflib.get_close_matches(old, list(mats.keys()), 5, 0.4)}")
    if new not in mats:
        raise ValueError(f"replacement material {new!r} not found (create it first, e.g. with procedural_material)")
    om, nm = mats[old], mats[new]
    pool = _objs(objects) if objects else list(bpy.data.objects)
    changed, slots = [], 0
    for o in pool:
        hit = False
        for s in o.material_slots:
            if s.material == om:
                s.material = nm
                slots += 1
                hit = True
        if hit:
            changed.append(o.name)
    removed = False
    if remove_old and om.users - (1 if om.use_fake_user else 0) == 0:
        mats.remove(om)
        removed = True
    return {"old": old, "new": new, "objects_changed": changed[:50], "object_count": len(changed),
            "slots_changed": slots, "old_removed": removed}


@recipe("shading", tags=("material", "report", "audit", "missing textures", "purge", "cleanup"))
def material_report(purge_unused=False, include_images=True, demo=False) -> dict:
    '''Audit materials: users, objects using each, image textures, MISSING texture files, empty slots, objects without
    material. purge_unused=True deletes materials (and images, if include_images) with zero users (fake-user kept).
    demo=True builds a sample (used, unused, and a material pointing to a missing texture).'''
    if demo:
        used = bpy.data.materials.new("Demo_Used")
        _demo_mesh("ReportDemo", "cube", 1.0).data.materials.append(used)
        bpy.data.materials.new("Demo_Unused")
        broken, nt, out = _new_material("Demo_MissingTex")
        img = bpy.data.images.new("missing_tex", 4, 4)
        img.source = "FILE"
        img.filepath = os.path.join(tempfile.gettempdir(), "blendpro_definitely_missing_tex.png")
        _node(nt, "ShaderNodeTexImage", image=img)
        _demo_mesh("ReportDemoNoMat", "cube", 1.0, (2, 0, 0))
        _demo_mesh("ReportDemoBroken", "cube", 1.0, (4, 0, 0)).data.materials.append(broken)
    users_of = {}
    empty_slots, no_material = [], []
    for o in bpy.data.objects:
        if o.type not in MESHLIKE:
            continue
        if not o.material_slots:
            no_material.append(o.name)
        for i, s in enumerate(o.material_slots):
            if s.material is None:
                empty_slots.append(f"{o.name}[{i}]")
            else:
                users_of.setdefault(s.material.name, []).append(o.name)
    mats, missing = [], []
    for m in bpy.data.materials:
        imgs = []
        if m.node_tree:
            for n in m.node_tree.nodes:
                img = getattr(n, "image", None)
                if img is None:
                    continue
                imgs.append(img.name)
                if img.source in {"FILE", "SEQUENCE", "TILED"} and not img.packed_file:
                    p = bpy.path.abspath(img.filepath)
                    if not p or not os.path.exists(p):
                        missing.append({"material": m.name, "image": img.name, "path": img.filepath})
        mats.append({"name": m.name, "users": m.users, "fake_user": m.use_fake_user,
                     "objects": sorted(set(users_of.get(m.name, [])))[:8], "images": imgs[:6]})
    unused = [m["name"] for m in mats if m["users"] == 0]
    purged = {"materials": [], "images": []}
    if purge_unused:
        for _ in range(3):  # removing materials can free images
            for m in list(bpy.data.materials):
                if m.users == 0 and not m.use_fake_user:
                    purged["materials"].append(m.name)
                    bpy.data.materials.remove(m)
            if include_images:
                for im in list(bpy.data.images):
                    if im.users == 0 and not im.use_fake_user:
                        purged["images"].append(im.name)
                        bpy.data.images.remove(im)
    return {"material_count": len(mats), "materials": mats[:60], "unused": unused, "missing_textures": missing,
            "empty_slots": empty_slots[:40], "objects_without_material": no_material[:40],
            "purged": purged if purge_unused else None}


# =============================================================== LIGHTING / WORLD

@recipe("shading", tags=("lighting", "three point", "studio", "area light", "product"))
def three_point_lighting(targets=None, key_energy=None, distance_factor=2.5, key_angle_deg=45.0,
                         elevation_deg=35.0, use_temperature=True, collection="Lighting", replace=True) -> dict:
    '''Key/fill/rim area-light rig scaled to the bounds of targets (default: all visible meshes).
    Front = direction to the active camera if any, else -Y (Blender's front view). Energies follow inverse-square
    (key_energy None = auto); fill = 40% and rim = 80% of key. Lights go in `collection`; replace=True removes an old rig.'''
    scene = _scene()
    objs = _objs(targets) if targets else _scene_meshes()
    c, size, radius, _ = _bounds(objs)
    if replace:
        for nm in ("Key Light", "Fill Light", "Rim Light"):
            o = bpy.data.objects.get(nm)
            if o:
                data = o.data
                bpy.data.objects.remove(o)
                if data and data.users == 0:
                    bpy.data.lights.remove(data)
    coll = bpy.data.collections.get(collection)
    if coll is None:
        coll = bpy.data.collections.new(collection)
        scene.collection.children.link(coll)
    if scene.camera:
        d = scene.camera.matrix_world.translation - c
        front = math.atan2(d.y, d.x) if d.xy.length > 1e-6 else -math.pi / 2
    else:
        front = -math.pi / 2
    dist = max(radius * distance_factor, 0.5)
    key_e = key_energy if key_energy is not None else 40.0 * dist ** 2
    spec = [("Key Light", key_angle_deg, elevation_deg, 1.0, 5200),
            ("Fill Light", -key_angle_deg * 1.4, elevation_deg * 0.45, 0.4, 6500),
            ("Rim Light", 180 - key_angle_deg * 0.4, elevation_deg + 15, 0.8, 7500)]
    out = []
    for nm, az, el, mult, kelvin in spec:
        a, e = front + math.radians(az), math.radians(el)
        pos = c + Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e))) * dist
        ld = bpy.data.lights.new(nm, "AREA")
        ld.shape = "DISK"
        ld.size = max(radius * 1.5, 0.25)
        ld.energy = key_e * mult
        if use_temperature and hasattr(ld, "use_temperature"):
            ld.use_temperature = True
            ld.temperature = kelvin
        lo = bpy.data.objects.new(nm, ld)
        lo.location = pos
        lo.rotation_euler = _look_at_quat(c - pos).to_euler()
        coll.objects.link(lo)
        out.append({"name": nm, "location": r(list(pos)), "energy": r(ld.energy, 1), "size": r(ld.size)})
    return {"lights": out, "center": r(list(c)), "distance": r(dist), "collection": coll.name}


def _studio_hdri_dir():
    return bpy.utils.system_resource("DATAFILES", path=os.path.join("studiolights", "world"))


@recipe("shading", tags=("world", "hdri", "environment", "lighting", "ibl"))
def hdri_world(path=None, builtin="forest", strength=1.0, rotation_deg=0.0, camera_background=None,
               world_name="HDRI_World") -> dict:
    '''Image-based lighting: environment texture world with rotation (Mapping Z) and strength.
    path: .hdr/.exr file; if None uses a Blender-bundled HDRI by `builtin` name (city, courtyard, forest, interior,
    night, studio, sunrise, sunset). camera_background=(r,g,b) shows a flat colour to the camera while the HDRI still
    lights the scene (use render film_transparent for alpha instead).'''
    if path:
        p = bpy.path.abspath(path)
        if not os.path.isfile(p):
            raise ValueError(f"HDRI not found: {p}")
    else:
        d = _studio_hdri_dir()
        avail = sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".exr")) if d and os.path.isdir(d) else []
        if builtin not in avail:
            raise ValueError(f"builtin HDRI {builtin!r} not available; options: {avail}")
        p = os.path.join(d, builtin + ".exr")
    img = bpy.data.images.load(p, check_existing=True)
    world, nt, out = _ensure_world(world_name)
    tc = _node(nt, "ShaderNodeTexCoord", (-900, 0))
    mp = _node(nt, "ShaderNodeMapping", (-700, 0))
    mp.inputs["Rotation"].default_value = (0, 0, math.radians(rotation_deg))
    env = _node(nt, "ShaderNodeTexEnvironment", (-450, 0), image=img)
    bg = _node(nt, "ShaderNodeBackground", (-150, 0), inputs={"Strength": strength})
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    surf = bg.outputs["Background"]
    if camera_background is not None:
        lp = _node(nt, "ShaderNodeLightPath", (-150, 300))
        bg2 = _node(nt, "ShaderNodeBackground", (-150, -200))
        bg2.inputs["Color"].default_value = _rgba(camera_background)
        mix = _node(nt, "ShaderNodeMixShader", (200, 0))
        nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
        nt.links.new(surf, mix.inputs[1])
        nt.links.new(bg2.outputs["Background"], mix.inputs[2])
        surf = mix.outputs["Shader"]
    nt.links.new(surf, out.inputs["Surface"])
    return {"world": world.name, "image": img.name, "path": p, "strength": strength, "rotation_deg": rotation_deg,
            "camera_background": camera_background is not None}


@recipe("shading", tags=("world", "sky", "nishita", "sun", "outdoor"))
def sky_world(sky_type="MULTIPLE_SCATTERING", sun_elevation_deg=25.0, sun_rotation_deg=0.0, altitude=0.0,
              air_density=1.0, aerosol_density=1.0, ozone_density=1.0, strength=1.0, sun_disc=True,
              add_sun_light=False, world_name="Sky_World") -> dict:
    '''Physical sky world (Blender 5.x Sky Texture: MULTIPLE_SCATTERING / SINGLE_SCATTERING = Nishita, or PREETHAM,
    HOSEK_WILKIE). sun_rotation_deg 0 puts the sun toward +Y. aerosol_density = haze/dust. add_sun_light=True also
    adds/updates a matching Sun lamp ("Sky Sun") for crisp shadows. Values are physically bright: lower strength or
    exposure if the render is blown out.'''
    world, nt, out = _ensure_world(world_name)
    sky = _node(nt, "ShaderNodeTexSky", (-400, 0))
    types = [e.identifier for e in sky.bl_rna.properties["sky_type"].enum_items]
    if sky_type not in types:
        raise ValueError(f"sky_type must be one of {types}")
    sky.sky_type = sky_type
    el, rot = math.radians(sun_elevation_deg), math.radians(sun_rotation_deg)
    for attr, val in (("sun_elevation", el), ("sun_rotation", rot), ("altitude", altitude),
                      ("air_density", air_density), ("aerosol_density", aerosol_density),
                      ("ozone_density", ozone_density), ("sun_disc", bool(sun_disc))):
        if hasattr(sky, attr):
            setattr(sky, attr, val)
    sun_dir = Vector((-math.cos(el) * math.sin(rot), math.cos(el) * math.cos(rot), math.sin(el)))
    if sky_type in {"PREETHAM", "HOSEK_WILKIE"}:
        sky.sun_direction = sun_dir
    bg = _node(nt, "ShaderNodeBackground", (-100, 0), inputs={"Strength": strength})
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    res = {"world": world.name, "sky_type": sky_type, "sun_elevation_deg": sun_elevation_deg,
           "sun_rotation_deg": sun_rotation_deg, "sun_direction": r(list(sun_dir))}
    if add_sun_light:
        so = bpy.data.objects.get("Sky Sun")
        if so is None:
            so = _link(bpy.data.objects.new("Sky Sun", bpy.data.lights.new("Sky Sun", "SUN")))
        so.data.energy = max(0.0, 4.0 * min(1.0, math.sin(max(el, 0)) * 1.5))
        so.data.angle = math.radians(0.545)
        so.rotation_euler = _look_at_quat(-sun_dir).to_euler()
        res["sun_light"] = {"name": so.name, "energy": r(so.data.energy)}
    return res


@recipe("shading", tags=("lighting", "sun", "time of day", "outdoor", "shadows"))
def sun_time_of_day(hour=10.0, day_of_year=172, latitude_deg=45.0, north_offset_deg=0.0, elevation_deg=None,
                    azimuth_deg=None, strength=None, angle_deg=0.53, name="Sun", auto_color=True,
                    sync_sky=True) -> dict:
    '''Place a Sun lamp from time of day + date + latitude (or explicit elevation/azimuth in degrees).
    North = +Y rotated by north_offset_deg; azimuth is compass (clockwise from north). strength None = auto (dimmer
    near horizon); auto_color warms low sun via colour temperature. sync_sky=True also aligns an existing Sky Texture.'''
    if elevation_deg is None or azimuth_deg is None:
        if not 0 <= hour <= 24:
            raise ValueError("hour must be 0..24")
        lat = math.radians(latitude_deg)
        decl = math.radians(23.44) * math.sin(math.radians(360 / 365 * (284 + day_of_year)))
        ha = math.radians(15 * (hour - 12))
        sin_el = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(ha)
        el = math.asin(max(-1, min(1, sin_el)))
        az = math.atan2(math.sin(ha), math.cos(ha) * math.sin(lat) - math.tan(decl) * math.cos(lat)) + math.pi
        el_d = math.degrees(el) if elevation_deg is None else elevation_deg
        az_d = (math.degrees(az) % 360) if azimuth_deg is None else azimuth_deg
    else:
        el_d, az_d = elevation_deg, azimuth_deg
    el, az = math.radians(el_d), math.radians(az_d + north_offset_deg)
    # compass azimuth -> Blender (north=+Y, east=+X)
    sun_dir = Vector((math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)))
    so = bpy.data.objects.get(name)
    if so is None:
        so = _link(bpy.data.objects.new(name, bpy.data.lights.new(name, "SUN")))
    elif so.type != "LIGHT" or so.data.type != "SUN":
        raise ValueError(f"{name!r} exists and is not a Sun light")
    ld = so.data
    below = el_d <= 0
    ld.energy = (0.0 if below else 4.0 * max(0.05, min(1.0, math.sin(el) * 1.5))) if strength is None else strength
    ld.angle = math.radians(angle_deg)
    if auto_color and hasattr(ld, "use_temperature"):
        ld.use_temperature = True
        ld.temperature = 2000 + 3800 * max(0.0, min(1.0, el_d / 45.0)) ** 0.5
    so.rotation_euler = _look_at_quat(-sun_dir).to_euler()
    synced = False
    w = _scene().world
    if sync_sky and w and w.node_tree:
        for n in w.node_tree.nodes:
            if n.bl_idname == "ShaderNodeTexSky":
                n.sun_elevation = max(el, 0.0)
                n.sun_rotation = math.atan2(-sun_dir.x, sun_dir.y) % (2 * math.pi)
                if n.sky_type in {"PREETHAM", "HOSEK_WILKIE"}:
                    n.sun_direction = sun_dir
                synced = True
    res = {"light": so.name, "elevation_deg": r(el_d, 2), "azimuth_deg": r(az_d % 360, 2), "energy": r(ld.energy),
           "temperature": r(getattr(ld, "temperature", 0), 0) if auto_color else None,
           "direction_to_sun": r(list(sun_dir)), "sky_synced": synced}
    if below:
        res["warning"] = "sun is below the horizon at this time; energy set to 0"
    return res


@recipe("shading", tags=("shadow catcher", "ground", "compositing", "cycles", "product"))
def shadow_catcher_ground(targets=None, size_factor=4.0, z=None, name="ShadowCatcher", film_transparent=True) -> dict:
    '''Ground plane that only shows shadows/reflections (Cycles shadow catcher) under targets (default all meshes),
    sized size_factor x their footprint at their lowest Z (or z). film_transparent=True renders alpha so the result
    composites over any background. EEVEE does not support shadow catchers - render with Cycles.'''
    objs = _objs(targets) if targets else _scene_meshes(exclude=(name,))
    c, size, radius, _ = _bounds(objs)
    zz = (c.z - size.z / 2) if z is None and objs else (z or 0.0)
    half = max(size.x, size.y, 0.5) * size_factor / 2 if objs else 5.0
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old)
    me = bpy.data.meshes.new(name)
    me.from_pydata([(-half, -half, 0), (half, -half, 0), (half, half, 0), (-half, half, 0)], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    for i, co in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
        uv.data[i].uv = co
    o = _link(bpy.data.objects.new(name, me))
    o.location = (c.x if objs else 0, c.y if objs else 0, zz)
    o.is_shadow_catcher = True
    if film_transparent:
        _scene().render.film_transparent = True
    return {"object": o.name, "size": r(half * 2), "z": r(zz), "is_shadow_catcher": True,
            "film_transparent": _scene().render.film_transparent, "engine_note": "shadow catcher works in Cycles only"}


# =============================================================== CAMERA

@recipe("shading", tags=("camera", "frame", "fit", "composition"))
def frame_objects_camera(targets=None, camera="Camera", lens=50.0, margin=0.08, direction=(0.9, -1.6, 0.75),
                         keep_rotation=False, ortho=False, set_active=True) -> dict:
    '''Aim and dolly a camera so targets (default all visible meshes) exactly fill the frame + margin.
    direction = vector from subject toward camera (default front-right, above). keep_rotation=True only dollies an
    existing camera. Exact fit for perspective & ortho using current render aspect; creates the camera if missing.'''
    if margin < 0 or margin > 3:
        raise ValueError("margin must be 0..3 (fraction of frame)")
    objs = _objs(targets) if targets else _scene_meshes()
    c, size, radius, corners = _bounds(objs)
    cam_obj = bpy.data.objects.get(camera)
    created = cam_obj is None
    cam_obj = _make_camera(camera, lens, ortho)
    if not (keep_rotation and not created):
        d = Vector(direction)
        if d.length < 1e-6:
            raise ValueError("direction must be non-zero")
        cam_obj.rotation_mode = "XYZ"
        cam_obj.rotation_euler = _look_at_quat(-d).to_euler()
    bpy.context.view_layer.update()
    dist = _fit_camera(cam_obj, corners, margin)
    if set_active:
        _scene().camera = cam_obj
    res = {"camera": cam_obj.name, "created": created, "location": r(list(cam_obj.location)),
           "rotation_deg": [r(math.degrees(a), 1) for a in cam_obj.rotation_euler], "distance": r(dist),
           "lens": lens, "framed": [o.name for o in objs][:20]}
    if ortho:
        res["ortho_scale"] = r(cam_obj.data.ortho_scale)
    if not objs:
        res["note"] = "no meshes found; framed a 2 m cube at the origin"
    return res


@recipe("shading", tags=("camera", "turntable", "animation", "orbit", "product"))
def turntable_setup(target=None, frames=120, mode="camera", start_frame=1, elevation_deg=20.0, lens=50.0,
                    margin=0.1) -> dict:
    '''Seamless 360-degree turntable over `frames` frames with linear keys.
    mode='camera': camera parented to a pivot empty at the subject centre (subject untouched, framed for every angle);
    mode='object': rotates the target object itself (target required). target=None (camera mode) frames all meshes.
    Sets scene frame range and active camera; render with render_still(frame=...) or an animation render.'''
    if frames < 2:
        raise ValueError("frames must be >= 2")
    if mode not in {"camera", "object"}:
        raise ValueError("mode must be 'camera' or 'object'")
    scene = _scene()
    objs = _objs(target) if target else _scene_meshes()
    c, size, radius, corners = _bounds(objs)
    end = start_frame + frames
    scene.frame_start, scene.frame_end = start_frame, end - 1
    if mode == "object":
        if not target:
            raise ValueError("mode='object' needs a target object")
        o = objs[0]
        z0 = o.rotation_euler.z
        o.rotation_mode = "XYZ"
        o.keyframe_insert("rotation_euler", index=2, frame=start_frame)
        o.rotation_euler.z = z0 + 2 * math.pi
        o.keyframe_insert("rotation_euler", index=2, frame=end)
        o.rotation_euler.z = z0
        _set_linear(o)
        return {"mode": "object", "object": o.name, "frame_start": start_frame, "frame_end": end - 1}
    pivot = bpy.data.objects.get("TurntablePivot") or _link(bpy.data.objects.new("TurntablePivot", None))
    pivot.location = c
    pivot.rotation_euler = (0, 0, 0)
    cam_obj = _make_camera("TurntableCam", lens)
    el = math.radians(elevation_deg)
    cam_obj.parent = None
    cam_obj.rotation_euler = _look_at_quat(-Vector((0, -math.cos(el), math.sin(el)))).to_euler()
    bpy.context.view_layer.update()
    # fit the union of corners at 8 rotations so the subject stays in frame all the way around
    pts = []
    for k in range(8):
        a = k * math.pi / 4
        ca, sa = math.cos(a), math.sin(a)
        for p in corners:
            q = p - c
            pts.append(c + Vector((q.x * ca - q.y * sa, q.x * sa + q.y * ca, q.z)))
    _fit_camera(cam_obj, pts, margin)
    bpy.context.view_layer.update()
    mw = cam_obj.matrix_world.copy()
    cam_obj.parent = pivot
    cam_obj.matrix_parent_inverse = pivot.matrix_world.inverted()
    cam_obj.matrix_world = mw
    if pivot.animation_data:
        pivot.animation_data_clear()
    pivot.keyframe_insert("rotation_euler", index=2, frame=start_frame)
    pivot.rotation_euler.z = 2 * math.pi
    pivot.keyframe_insert("rotation_euler", index=2, frame=end)
    pivot.rotation_euler.z = 0
    _set_linear(pivot)
    scene.camera = cam_obj
    return {"mode": "camera", "pivot": pivot.name, "camera": cam_obj.name, "center": r(list(c)),
            "frame_start": start_frame, "frame_end": end - 1, "camera_distance": r((cam_obj.matrix_world.translation - c).length)}


@recipe("shading", tags=("camera", "depth of field", "dof", "focus", "bokeh"))
def camera_dof_focus(focus_object=None, camera=None, fstop=2.8, focus_distance=None, demo=False) -> dict:
    '''Enable depth of field focused on an object (tracks it when it moves) or at a fixed focus_distance (m).
    camera None = scene camera (one is created and framed if the scene has none). Lower fstop = blurrier background
    (1.4-4 typical). demo=True adds a sphere to focus on.'''
    if fstop <= 0:
        raise ValueError("fstop must be > 0")
    if demo and focus_object is None:
        focus_object = _demo_mesh("DOFDemo", "sphere", 1.0).name
    fo = get_obj(focus_object) if focus_object else None
    if fo is None and focus_distance is None:
        raise ValueError("give focus_object or focus_distance")
    scene = _scene()
    if camera:
        cam_obj = get_obj(camera)
        if cam_obj.type != "CAMERA":
            raise ValueError(f"{camera} is not a camera")
    elif scene.camera:
        cam_obj = scene.camera
    else:
        frame_objects_camera(targets=[fo.name] if fo else None, margin=0.3)
        cam_obj = scene.camera
    bpy.context.view_layer.update()
    dof = cam_obj.data.dof
    dof.use_dof = True
    dof.aperture_fstop = fstop
    dof.focus_object = fo
    if focus_distance is not None:
        dof.focus_distance = focus_distance
    dist = (fo.matrix_world.translation - cam_obj.matrix_world.translation).length if fo else focus_distance
    return {"camera": cam_obj.name, "focus_object": fo.name if fo else None, "fstop": fstop,
            "focus_distance": r(dist)}


# =============================================================== RENDER / OUTPUT

_PRESETS = {"draft": (25, 8, 16), "preview": (50, 16, 64), "final": (100, 64, 256)}


@recipe("shading", tags=("render", "settings", "preset", "color management", "agx", "samples"))
def set_render_preset(preset="preview", engine="EEVEE", resolution=(1920, 1080), samples=None, denoise=True,
                      view_transform="AgX", look=None, exposure=0.0, film_transparent=False, file_format="PNG",
                      raytracing=None) -> dict:
    '''Apply a coherent render configuration: preset draft|preview|final sets resolution % and samples per engine
    (EEVEE 8/16/64, Cycles 16/64/256 with adaptive sampling + denoise). view_transform 'AgX'|'Standard'|'Filmic'|
    'Khronos PBR Neutral'; look e.g. 'AgX - Punchy'. raytracing (EEVEE) defaults on for 'final'. Persistent change.'''
    if preset not in _PRESETS:
        raise ValueError(f"preset must be one of {list(_PRESETS)}")
    scene = _scene()
    rs = scene.render
    rs.engine = _engine_id(engine)
    pct, ee_s, cy_s = _PRESETS[preset]
    if resolution:
        rs.resolution_x, rs.resolution_y = int(resolution[0]), int(resolution[1])
    rs.resolution_percentage = pct
    applied = {"engine": rs.engine, "resolution": [rs.resolution_x, rs.resolution_y], "percentage": pct}
    if rs.engine == "CYCLES":
        cy = scene.cycles
        cy.samples = int(samples or cy_s)
        cy.preview_samples = min(cy.samples, 32)
        cy.use_adaptive_sampling = True
        cy.use_denoising = bool(denoise)
        applied.update(samples=cy.samples, denoise=cy.use_denoising)
    elif rs.engine.startswith("BLENDER_EEVEE"):
        ee = scene.eevee
        ee.taa_render_samples = int(samples or ee_s)
        ee.taa_samples = min(ee.taa_render_samples, 16)
        rt = (preset == "final") if raytracing is None else bool(raytracing)
        if hasattr(ee, "use_raytracing"):
            ee.use_raytracing = rt
        applied.update(samples=ee.taa_render_samples, raytracing=rt)
    vs = scene.view_settings
    try:
        vs.view_transform = view_transform
    except TypeError as e:
        raise ValueError(f"view_transform {view_transform!r} invalid: {e}")
    vs.look = look or "None"
    vs.exposure = exposure
    rs.film_transparent = bool(film_transparent)
    try:
        rs.image_settings.file_format = file_format
    except TypeError as e:
        raise ValueError(f"file_format {file_format!r} invalid: {e}")
    if film_transparent and file_format in {"PNG", "OPEN_EXR", "TIFF"}:
        rs.image_settings.color_mode = "RGBA"
    applied.update(view_transform=vs.view_transform, look=vs.look, exposure=exposure,
                   film_transparent=rs.film_transparent, file_format=rs.image_settings.file_format)
    return applied


@recipe("shading", tags=("render", "still", "output", "image", "preview"))
def render_still(filepath="//render.png", resolution=None, engine=None, samples=None, camera=None, frame=None,
                 transparent=None, file_format="PNG") -> dict:
    '''Render one frame to filepath and return path + pixel size; scene settings are restored afterwards.
    resolution (w,h) / engine (EEVEE|CYCLES|WORKBENCH) / samples / transparent are temporary overrides.
    No scene camera? A temporary camera framing all meshes is used and deleted. '//' paths need a saved .blend
    (otherwise the system temp dir is used).'''
    scene = _scene()
    path = filepath
    if path.startswith("//") and not bpy.data.filepath:
        path = os.path.join(tempfile.gettempdir(), path[2:])
    path = os.path.abspath(bpy.path.abspath(path))
    ext = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr", "TIFF": ".tif", "WEBP": ".webp"}.get(file_format)
    if ext is None:
        raise ValueError("file_format must be PNG, JPEG, OPEN_EXR, TIFF or WEBP")
    if not path.lower().endswith(ext):
        path += ext
    os.makedirs(os.path.dirname(path), exist_ok=True)
    snap = _snapshot_render(scene)
    temp_cam = None
    try:
        if engine:
            scene.render.engine = _engine_id(engine)
        _set_samples(scene, samples)
        if resolution:
            scene.render.resolution_x, scene.render.resolution_y = int(resolution[0]), int(resolution[1])
            scene.render.resolution_percentage = 100
        if transparent is not None:
            scene.render.film_transparent = bool(transparent)
        scene.render.image_settings.file_format = file_format
        if camera:
            co = get_obj(camera)
            if co.type != "CAMERA":
                raise ValueError(f"{camera} is not a camera")
            scene.camera = co
        if scene.camera is None:
            frame_objects_camera(camera="BP_TempRenderCam")
            temp_cam = bpy.data.objects["BP_TempRenderCam"]
        if frame is not None:
            scene.frame_set(int(frame))
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        used = {"engine": scene.render.engine, "camera": scene.camera.name if scene.camera else None,
                "frame": scene.frame_current}
    finally:
        _restore_render(scene, snap)
        if temp_cam is not None:
            cd = temp_cam.data
            bpy.data.objects.remove(temp_cam)
            if cd.users == 0:
                bpy.data.cameras.remove(cd)
    if not os.path.isfile(path):
        raise RuntimeError(f"render finished but no file at {path}")
    return {"path": path, "size": _image_size_on_disk(path), "bytes": os.path.getsize(path), **used,
            "temp_camera": temp_cam is not None}


_GLARE = {"BLOOM": "Bloom", "GHOSTS": "Ghosts", "STREAKS": "Streaks", "FOG_GLOW": "Fog Glow",
          "SIMPLE_STAR": "Simple Star", "SUN_BEAMS": "Sun Beams"}


@recipe("shading", tags=("compositor", "glare", "bloom", "vignette", "lens distortion", "post"))
def compositor_effects(glare_type="Fog Glow", glare_threshold=1.0, glare_strength=1.0, glare_size=0.5,
                       vignette=0.3, lens_distortion=0.0, dispersion=0.0, exposure=0.0) -> dict:
    '''Rebuild the scene compositor with common post FX: exposure -> glare/bloom -> lens distortion/dispersion -> vignette.
    Blender 5.x node-group compositor (scene.compositing_node_group). glare_type Bloom|Fog Glow|Streaks|Ghosts|
    Simple Star|Sun Beams or None; vignette 0..1 darkening at the corners (0 = off). Replaces the existing tree.'''
    scene = _scene()
    if not hasattr(scene, "compositing_node_group"):
        raise RuntimeError("this recipe targets Blender 5.x (scene.compositing_node_group)")
    gt = None
    if glare_type:
        gt = _GLARE.get(str(glare_type).upper().replace(" ", "_"), glare_type)
        if gt not in _GLARE.values():
            raise ValueError(f"glare_type must be one of {list(_GLARE.values())} or None")
    if not 0 <= vignette <= 1:
        raise ValueError("vignette must be 0..1")
    ng = scene.compositing_node_group
    if ng is None:
        ng = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
        scene.compositing_node_group = ng
    ng.nodes.clear()
    if not any(getattr(i, "in_out", "") == "OUTPUT" for i in ng.interface.items_tree):
        ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    L = ng.links.new
    rl = _node(ng, "CompositorNodeRLayers", (-900, 0))
    rl.scene = scene
    img = rl.outputs["Image"]
    x = -650
    chain = ["Render Layers"]
    if exposure:
        ex = _node(ng, "CompositorNodeExposure", (x, 0))
        ex.inputs["Exposure"].default_value = exposure
        L(img, ex.inputs["Image"])
        img, x = ex.outputs["Image"], x + 220
        chain.append("Exposure")
    if gt:
        gl = _node(ng, "CompositorNodeGlare", (x, 0))
        gl.inputs["Type"].default_value = gt
        gl.inputs["Threshold"].default_value = glare_threshold
        gl.inputs["Strength"].default_value = glare_strength
        gl.inputs["Size"].default_value = glare_size
        L(img, gl.inputs["Image"])
        img, x = gl.outputs["Image"], x + 220
        chain.append(f"Glare({gt})")
    if lens_distortion or dispersion:
        ld = _node(ng, "CompositorNodeLensdist", (x, 0))
        ld.inputs["Distortion"].default_value = lens_distortion
        ld.inputs["Dispersion"].default_value = dispersion
        L(img, ld.inputs["Image"])
        img, x = ld.outputs["Image"], x + 220
        chain.append("LensDistortion")
    if vignette > 0:
        ic = _node(ng, "CompositorNodeImageCoordinates", (x, -300))
        L(img, ic.inputs["Image"])
        sub = _node(ng, "ShaderNodeVectorMath", (x + 200, -300), operation="SUBTRACT")
        sub.inputs[1].default_value = (0.5, 0.5, 0.0)
        ln = _node(ng, "ShaderNodeVectorMath", (x + 400, -300), operation="LENGTH")
        mr = _node(ng, "ShaderNodeMapRange", (x + 600, -300))
        mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = 0.3, 0.75
        mr.inputs["To Min"].default_value, mr.inputs["To Max"].default_value = 0.0, vignette
        L(ic.outputs["Normalized"], sub.inputs[0])
        L(sub.outputs["Vector"], ln.inputs[0])
        L(ln.outputs["Value"], mr.inputs["Value"])
        m, a, b, res = _mix_rgb(ng, "MIX", (x + 800, 0))
        b.default_value = (0, 0, 0, 1)
        L(mr.outputs["Result"], _sock(m.inputs, "Factor_Float"))
        L(img, a)
        img = res
        chain.append("Vignette")
    go = _node(ng, "NodeGroupOutput", (x + 1050, 0))
    L(img, go.inputs[0])
    vw = _node(ng, "CompositorNodeViewer", (x + 1050, -200))
    L(img, vw.inputs["Image"])
    scene.render.use_compositing = True
    invalid = [f"{l.from_node.name}->{l.to_node.name}" for l in ng.links if not l.is_valid]
    return {"tree": ng.name, "chain": chain, "nodes": len(ng.nodes), "invalid_links": invalid}


@recipe("shading", tags=("outline", "toon", "freestyle", "inverted hull", "npr"))
def outline_effect(method="inverted_hull", target=None, thickness=0.02, line_px=1.5, color=(0, 0, 0)) -> dict:
    '''Ink outlines. method='inverted_hull': Solidify (flipped, outward) + backface-culled outline material on target -
    real-time, visible in viewport/EEVEE/Cycles and exports to game engines/XR; thickness in metres.
    method='freestyle': render-time Freestyle lines for the whole scene (line_px pixels; silhouette/border/crease).
    target=None with inverted_hull creates a demo sphere.'''
    col = _rgba(color)
    if method == "freestyle":
        scene = _scene()
        scene.render.use_freestyle = True
        scene.render.line_thickness_mode = "ABSOLUTE"
        scene.render.line_thickness = line_px
        vl = bpy.context.view_layer
        vl.use_freestyle = True
        fs = vl.freestyle_settings
        ls = fs.linesets[0] if len(fs.linesets) else fs.linesets.new("LineSet")
        ls.select_by_visibility = True
        ls.select_by_edge_types = True
        ls.select_silhouette = ls.select_border = ls.select_crease = True
        lst = ls.linestyle or bpy.data.linestyles.new("LineStyle")
        ls.linestyle = lst
        lst.color = col[:3]
        lst.thickness = line_px
        return {"method": "freestyle", "line_px": line_px, "lineset": ls.name, "linestyle": lst.name}
    if method != "inverted_hull":
        raise ValueError("method must be 'inverted_hull' or 'freestyle'")
    obj = get_obj(target) if target else _demo_mesh("OutlineDemo", "sphere", 2.0)
    if obj.type != "MESH":
        raise ValueError(f"{obj.name} must be a mesh")
    mname = "Outline_Ink"
    mat = bpy.data.materials.get(mname)
    if mat is None:
        mat, nt, out = _new_material(mname)
        geo = _node(nt, "ShaderNodeNewGeometry", (-400, 200))
        em = _node(nt, "ShaderNodeEmission", (-200, 0))
        tr = _node(nt, "ShaderNodeBsdfTransparent", (-200, -150))
        mix = _node(nt, "ShaderNodeMixShader", (200, 0))
        nt.links.new(geo.outputs["Backfacing"], mix.inputs["Fac"])
        nt.links.new(em.outputs["Emission"], mix.inputs[1])
        nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        mat.use_backface_culling = True
    em = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeEmission")
    em.inputs["Color"].default_value = col
    if not obj.material_slots:
        obj.data.materials.append(bpy.data.materials.new(f"{obj.name}_Base"))
    if mname not in [s.material.name for s in obj.material_slots if s.material]:
        obj.data.materials.append(mat)
    idx = [s.material.name if s.material else "" for s in obj.material_slots].index(mname)
    mod = obj.modifiers.get("Outline") or obj.modifiers.new("Outline", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_rim = False
    mod.material_offset = idx
    mod.material_offset_rim = idx
    return {"method": "inverted_hull", "object": obj.name, "modifier": mod.name, "material": mname,
            "material_index": idx, "thickness": thickness}


# =============================================================== BAKING (Cycles)

_BAKE_ALIASES = {"BASECOLOR": "DIFFUSE", "ALBEDO": "DIFFUSE", "COLOR": "DIFFUSE", "DIFFUSE": "DIFFUSE",
                 "AO": "AO", "NORMAL": "NORMAL", "ROUGHNESS": "ROUGHNESS", "EMIT": "EMIT", "EMISSION": "EMIT",
                 "COMBINED": "COMBINED", "LIGHTMAP": "COMBINED", "SHADOW": "SHADOW", "GLOSSY": "GLOSSY"}
_NONCOLOR = {"AO", "NORMAL", "ROUGHNESS", "SHADOW"}


class _BakeContext:
    """Temporarily switches to Cycles + bake settings + selection; restores everything on exit."""

    def __init__(self, active, selected, samples, margin):
        self.active, self.selected, self.samples, self.margin = active, selected, samples, margin

    def __enter__(self):
        sc = _scene()
        vl = bpy.context.view_layer
        b = sc.render.bake
        self.saved = {"engine": sc.render.engine, "samples": sc.cycles.samples,
                      "bake": {k: getattr(b, k) for k in ("use_selected_to_active", "use_cage", "cage_extrusion",
                                                          "max_ray_distance", "margin", "use_clear", "target",
                                                          "use_pass_direct", "use_pass_indirect", "use_pass_color",
                                                          "normal_space", "cage_object")},
                      "sel": {o.name: o.select_get() for o in vl.objects}, "active": vl.objects.active}
        sc.render.engine = "CYCLES"
        sc.cycles.samples = int(self.samples)
        b.margin = int(self.margin)
        b.target = "IMAGE_TEXTURES"
        b.use_clear = True
        for o in vl.objects:
            o.select_set(o in self.selected)
        vl.objects.active = self.active
        return self

    def bake(self, **kw):
        ov = {"object": self.active, "active_object": self.active, "selected_objects": list(self.selected),
              "selected_editable_objects": list(self.selected)}
        with bpy.context.temp_override(**ov):
            res = bpy.ops.object.bake(**kw)
        if "FINISHED" not in res:
            raise RuntimeError(f"bake did not finish: {res}")

    def __exit__(self, *exc):
        sc = _scene()
        vl = bpy.context.view_layer
        sc.render.engine = self.saved["engine"]
        sc.cycles.samples = self.saved["samples"]
        for k, v in self.saved["bake"].items():
            try:
                setattr(sc.render.bake, k, v)
            except Exception:
                pass
        for o in vl.objects:
            o.select_set(self.saved["sel"].get(o.name, False))
        vl.objects.active = self.saved["active"]
        return False


def _with_bake_nodes(obj, img):
    """Add an active Image Texture node (image=img) to every material of obj. Returns cleanup callable."""
    added, temp_mats, prev_active = [], [], []
    if not obj.material_slots:
        tm = bpy.data.materials.new("BP_TempBakeMat")
        obj.data.materials.append(tm)
        temp_mats.append(("append", tm))
    for i, s in enumerate(obj.material_slots):
        if s.material is None:
            tm = bpy.data.materials.new("BP_TempBakeMat")
            s.material = tm
            temp_mats.append(("slot", i, tm))
    seen = set()
    for s in obj.material_slots:
        m = s.material
        if m.name in seen:
            continue
        seen.add(m.name)
        if getattr(m, "use_nodes", True) is False:
            m.use_nodes = True
        nt = m.node_tree
        prev_active.append((nt, nt.nodes.active))
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = img
        n.location = (-800, 600)
        n.select = True
        nt.nodes.active = n
        added.append((nt, n))

    def cleanup():
        for nt, n in added:
            nt.nodes.remove(n)
        for nt, a in prev_active:
            try:
                nt.nodes.active = a
            except Exception:
                pass
        for t in temp_mats:
            if t[0] == "append":
                idx = list(obj.data.materials).index(t[1])
                obj.data.materials.pop(index=idx)
                bpy.data.materials.remove(t[1])
            else:
                obj.material_slots[t[1]].material = None
                bpy.data.materials.remove(t[2])
    return cleanup


def _save_image(img, path):
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    return {"path": path, "size": list(img.size), "exists": os.path.isfile(path)}


def _require_uv(obj, uv_map):
    if obj.type != "MESH":
        raise ValueError(f"{obj.name} must be a mesh")
    if not obj.data.uv_layers:
        raise ValueError(f"{obj.name} has no UV map; unwrap it first (e.g. Smart UV Project) before baking")
    if uv_map and uv_map not in obj.data.uv_layers:
        raise ValueError(f"UV map {uv_map!r} not on {obj.name}: {[u.name for u in obj.data.uv_layers]}")


@recipe("shading", tags=("bake", "cycles", "ao", "diffuse", "roughness", "texture atlas", "game ready", "xr"))
def bake_maps(target=None, maps=("AO", "DIFFUSE"), resolution=1024, samples=16, output_dir=None, margin=8,
              apply_material=False, uv_map=None) -> dict:
    '''Bake texture maps from an object's materials (Cycles) into one image per map and save PNGs.
    maps: AO, DIFFUSE/BASECOLOR (colour only), ROUGHNESS, NORMAL, EMIT, COMBINED (full lighting/lightmap), SHADOW, GLOSSY.
    All material slots bake into the SAME image = texture atlas for multi-material objects (needs non-overlapping UVs).
    apply_material=True replaces all slots with one '<obj>_Baked' material using the maps (game/XR-ready).
    target=None creates a demo object. Restores engine/samples/selection; temp nodes are removed.'''
    if target is None:
        obj = _demo_mesh("BakeDemo", "sphere", 2.0, (0, 0, 1))
        procedural_material("wood", name="BakeDemoWood", target=obj.name)
        _demo_mesh("BakeDemoGround", "plane", 6.0)
    else:
        obj = get_obj(target)
    _require_uv(obj, uv_map)
    if isinstance(maps, str):
        maps = [maps]
    kinds = []
    for m in maps:
        k = _BAKE_ALIASES.get(str(m).upper())
        if k is None:
            raise ValueError(f"unknown map {m!r}; options: {sorted(_BAKE_ALIASES)}")
        if k not in kinds:
            kinds.append(k)
    if not 8 <= resolution <= 16384:
        raise ValueError("resolution must be 8..16384")
    d = _out_dir(output_dir, "baked")
    files = {}
    images = {}
    with _BakeContext(obj, [obj], samples, margin) as bc:
        b = _scene().render.bake
        b.use_selected_to_active = False
        for k in kinds:
            nm = f"{obj.name}_{k.lower()}"
            old = bpy.data.images.get(nm)
            if old:
                bpy.data.images.remove(old)
            img = bpy.data.images.new(nm, resolution, resolution, alpha=False)
            img.colorspace_settings.name = "Non-Color" if k in _NONCOLOR else "sRGB"
            cleanup = _with_bake_nodes(obj, img)
            try:
                kw = {"type": k, "margin": int(margin), "uv_layer": uv_map or ""}
                if k == "DIFFUSE":
                    b.use_pass_direct, b.use_pass_indirect, b.use_pass_color = False, False, True
                    kw["pass_filter"] = {"COLOR"}
                elif k == "NORMAL":
                    kw["normal_space"] = "TANGENT"
                bc.bake(**kw)
            finally:
                cleanup()
            files[k] = _save_image(img, os.path.join(d, nm + ".png"))
            images[k] = img
    res = {"object": obj.name, "maps": files, "resolution": resolution, "output_dir": d}
    if apply_material:
        mat, nt, out = _new_material(f"{obj.name}_Baked")
        bsdf = _node(nt, "ShaderNodeBsdfPrincipled", (250, 0))
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        tex = {}
        for i, (k, img) in enumerate(images.items()):
            tex[k] = _node(nt, "ShaderNodeTexImage", (-600, 400 - 280 * i), image=img)
        if "COMBINED" in tex and "DIFFUSE" not in tex:
            em = _node(nt, "ShaderNodeEmission", (250, -300))
            nt.links.new(tex["COMBINED"].outputs["Color"], em.inputs["Color"])
            nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
        if "DIFFUSE" in tex:
            col = tex["DIFFUSE"].outputs["Color"]
            if "AO" in tex:
                mx, a, bb, rr = _mix_rgb(nt, "MULTIPLY", (-200, 400))
                nt.links.new(col, a)
                nt.links.new(tex["AO"].outputs["Color"], bb)
                col = rr
            nt.links.new(col, bsdf.inputs["Base Color"])
        elif "AO" in tex and "COMBINED" not in tex:
            nt.links.new(tex["AO"].outputs["Color"], bsdf.inputs["Base Color"])
        if "ROUGHNESS" in tex:
            nt.links.new(tex["ROUGHNESS"].outputs["Color"], bsdf.inputs["Roughness"])
        if "NORMAL" in tex:
            nm_ = _node(nt, "ShaderNodeNormalMap", (0, -300))
            nt.links.new(tex["NORMAL"].outputs["Color"], nm_.inputs["Color"])
            nt.links.new(nm_.outputs["Normal"], bsdf.inputs["Normal"])
        if "EMIT" in tex:
            nt.links.new(tex["EMIT"].outputs["Color"], bsdf.inputs["Emission Color"])
            bsdf.inputs["Emission Strength"].default_value = 1.0
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        obj.data.polygons.foreach_set("material_index", [0] * len(obj.data.polygons))
        obj.data.update()
        res["baked_material"] = mat.name
    return res


@recipe("shading", tags=("bake", "normal map", "high to low", "cycles", "retopo", "game ready"))
def bake_normal_high_to_low(high=None, low=None, resolution=2048, extrusion=None, max_ray_distance=0.0,
                            cage_object=None, samples=1, output_path=None, apply_to_low=True) -> dict:
    '''Bake a tangent-space normal map from a high-poly mesh onto a low-poly mesh's UVs (Cycles selected-to-active).
    extrusion None = 1.5% of the low mesh size (raise it if you see holes/artefacts; or pass a cage_object).
    apply_to_low=True wires the map (Normal Map node) into the low object's Principled BSDF (creating a material if
    needed). high/low None creates a demo pair (smooth sphere onto a 12-segment sphere). Restores scene state.'''
    if high is None and low is None:
        lo = _demo_mesh("NormalBakeLow", "sphere", 2.0, segments=12)
        for p in lo.data.polygons:
            p.use_smooth = True
        hi = _demo_mesh("NormalBakeHigh", "sphere", 2.0, segments=96)
        for v in hi.data.vertices:  # bumpy surface detail to transfer
            n = v.co.normalized()
            v.co = n * (1.0 + 0.04 * math.sin(n.x * 14) * math.sin(n.y * 14) * math.sin(n.z * 14 + 1))
        hi.data.update()
    else:
        if not high or not low:
            raise ValueError("give both high and low object names")
        hi, lo = get_obj(high), get_obj(low)
    if hi == lo:
        raise ValueError("high and low must be different objects")
    _require_uv(lo, None)
    if hi.type not in MESHLIKE:
        raise ValueError(f"{hi.name} must be a mesh-like object")
    cage = get_obj(cage_object) if cage_object else None
    _, size, radius, _ = _bounds([lo])
    ext = extrusion if extrusion is not None else max(radius * 2 * 0.015, 0.001)
    path = bpy.path.abspath(output_path) if output_path else os.path.join(_out_dir(None, "baked"), f"{lo.name}_normal.png")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    nm = f"{lo.name}_normal"
    old = bpy.data.images.get(nm)
    if old:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(nm, resolution, resolution, alpha=False)
    img.colorspace_settings.name = "Non-Color"
    img.generated_color = (0.5, 0.5, 1.0, 1.0)
    with _BakeContext(lo, [hi, lo], samples, 16) as bc:
        b = _scene().render.bake
        b.use_selected_to_active = True
        b.use_cage = cage is not None
        b.cage_object = cage
        b.cage_extrusion = ext
        b.max_ray_distance = max_ray_distance
        cleanup = _with_bake_nodes(lo, img)
        try:
            bc.bake(type="NORMAL", normal_space="TANGENT", use_selected_to_active=True, cage_extrusion=ext,
                    max_ray_distance=max_ray_distance, use_cage=cage is not None,
                    cage_object=cage.name if cage else "", margin=16)
        finally:
            cleanup()
    saved = _save_image(img, path)
    res = {"high": hi.name, "low": lo.name, "image": img.name, "extrusion": r(ext, 4), **saved}
    if apply_to_low:
        if not lo.material_slots or lo.material_slots[0].material is None:
            m = bpy.data.materials.new(f"{lo.name}_Mat")
            if lo.material_slots:
                lo.material_slots[0].material = m
            else:
                lo.data.materials.append(m)
        wired = []
        for s in lo.material_slots:
            m = s.material
            if m is None or m.name in wired:
                continue
            nt = m.node_tree
            bsdf = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
            if bsdf is None:
                continue
            t = nt.nodes.new("ShaderNodeTexImage")
            t.image, t.location = img, (bsdf.location.x - 700, bsdf.location.y - 400)
            nmn = nt.nodes.new("ShaderNodeNormalMap")
            nmn.location = (bsdf.location.x - 300, bsdf.location.y - 400)
            nt.links.new(t.outputs["Color"], nmn.inputs["Color"])
            nt.links.new(nmn.outputs["Normal"], bsdf.inputs["Normal"])
            wired.append(m.name)
        res["applied_to"] = wired
    return res
