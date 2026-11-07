"""Multi-view contact-sheet renders: one small image instead of many screenshots."""
import math
import os
import tempfile
import bpy
import mathutils
from .util import world_bbox, eevee_engine_id, find_view3d

VIEW_DIRS = {
    # direction the camera looks along, and its up vector
    "front": ((0, 1, 0), (0, 0, 1)),     # camera at -Y looking +Y
    "back": ((0, -1, 0), (0, 0, 1)),
    "right": ((-1, 0, 0), (0, 0, 1)),    # camera at +X looking -X
    "left": ((1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, -1), (0, 1, 0)),
    "bottom": ((0, 0, 1), (0, -1, 0)),
    "iso": ((-1, 1, -0.8), (0, 0, 1)),   # from front-right-above
    "iso_back": ((1, -1, -0.8), (0, 0, 1)),
}


def _look_rotation(direction, up):
    d = mathutils.Vector(direction).normalized()
    return d.to_track_quat("-Z", "Y") if abs(d.dot(mathutils.Vector(up))) > 0.999 else _track(d, mathutils.Vector(up))


def _track(d, up):
    z = -d
    x = up.cross(z).normalized()
    y = z.cross(x)
    m = mathutils.Matrix((x, y, z)).transposed()
    return m.to_quaternion()


def _target_bounds(objs, dg):
    from .util import real_meshes
    expanded = []
    for o in objs:  # an empty/armature root stands for its renderable children
        expanded.append(o)
        if o.type in {"EMPTY", "ARMATURE"}:
            expanded.extend(o.children_recursive)
    geo_types = {"CURVE", "FONT", "SURFACE", "META", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}
    objs = real_meshes(expanded) + [o for o in expanded if o.type in geo_types and not o.hide_render]
    mins, maxs = [], []
    for o in objs:
        bb = world_bbox(o, dg)
        if bb:
            mins.append(bb[0]); maxs.append(bb[1])
    if not mins:
        return mathutils.Vector((-1, -1, -1)), mathutils.Vector((1, 1, 1))
    mn = mathutils.Vector((min(v.x for v in mins), min(v.y for v in mins), min(v.z for v in mins)))
    mx = mathutils.Vector((max(v.x for v in maxs), max(v.y for v in maxs), max(v.z for v in maxs)))
    return mn, mx


def render_views(views=("front", "right", "top", "iso"), size=320, objects=None, engine="workbench",
                 color="material", filepath=None, samples=16, wireframe=False, xray=False):
    scene = bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    views = list(views)
    targets = [bpy.data.objects[n] for n in objects] if objects else [o for o in scene.objects if o.visible_get() and o.type not in {"CAMERA", "LIGHT"}]
    mn, mx = _target_bounds(targets, dg)
    center = (mn + mx) / 2
    radius = max((mx - mn).length / 2, 1e-3)

    rd = scene.render
    saved = {
        "engine": rd.engine, "res_x": rd.resolution_x, "res_y": rd.resolution_y, "pct": rd.resolution_percentage,
        "camera": scene.camera, "film": rd.film_transparent, "filepath": rd.filepath,
        "fmt": rd.image_settings.file_format,
    }
    shading = scene.display.shading
    saved_shading = {k: getattr(shading, k) for k in ("light", "color_type", "show_cavity", "show_object_outline", "show_xray", "show_shadows")}
    saved_eevee = getattr(scene.eevee, "taa_render_samples", None)
    saved_cycles = scene.cycles.samples if hasattr(scene, "cycles") else None
    hidden = []
    if objects:
        keep = {o.name for o in targets}
        for o in scene.objects:
            if o.name not in keep and not o.hide_render and o.type not in {"LIGHT"}:
                o.hide_render = True
                hidden.append(o)
    wire_mods = []
    cam_data = bpy.data.cameras.new(".bp_cam")
    cam_obj = bpy.data.objects.new(".bp_cam", cam_data)
    scene.collection.objects.link(cam_obj)
    outdir = tempfile.mkdtemp(prefix="blendpro_views_")
    paths = []
    try:
        eng = {"workbench": "BLENDER_WORKBENCH", "eevee": eevee_engine_id(), "cycles": "CYCLES"}.get(engine.lower(), rd.engine)
        rd.engine = eng
        rd.resolution_percentage = 100
        rd.image_settings.file_format = "PNG"
        if eng == "BLENDER_WORKBENCH":
            shading.light = "STUDIO"
            shading.color_type = {"material": "MATERIAL", "object": "OBJECT", "random": "RANDOM", "single": "SINGLE",
                                  "texture": "TEXTURE", "vertex": "VERTEX"}.get(color, "MATERIAL")
            shading.show_cavity = True
            shading.show_object_outline = True
            shading.show_xray = bool(xray)
        elif eng == "CYCLES":
            scene.cycles.samples = samples
        elif saved_eevee is not None:
            scene.eevee.taa_render_samples = samples
        if wireframe:
            for o in targets:
                if o.type == "MESH":
                    m = o.modifiers.new(".bp_wire", "WIREFRAME")
                    m.thickness = radius * 0.004
                    m.use_replace = False
                    wire_mods.append((o, m))
        scene.camera = cam_obj
        for v in views:
            if v == "camera":
                if saved["camera"] is None:
                    continue
                scene.camera = saved["camera"]
                aspect = saved["res_x"] / max(saved["res_y"], 1)
                rd.resolution_x = size if aspect >= 1 else int(size * aspect)
                rd.resolution_y = int(size / aspect) if aspect >= 1 else size
            else:
                scene.camera = cam_obj
                rd.resolution_x = rd.resolution_y = size
                direction, up = VIEW_DIRS[v]
                d = mathutils.Vector(direction).normalized()
                q = _track(d, mathutils.Vector(up)) if abs(d.dot(mathutils.Vector(up))) < 0.999 else d.to_track_quat("-Z", "Y")
                cam_obj.rotation_mode = "QUATERNION"
                cam_obj.rotation_quaternion = q
                if v.startswith("iso"):
                    cam_data.type = "PERSP"
                    cam_data.lens = 50
                    fov = cam_data.angle
                    dist = radius / math.sin(fov / 2) * 1.05
                    cam_obj.location = center - d * dist
                else:
                    cam_data.type = "ORTHO"
                    # ortho scale from extents perpendicular to view
                    ext = mx - mn
                    axes = [i for i in range(3) if abs(d[i]) < 0.5]
                    cam_data.ortho_scale = max(max(ext[i] for i in axes) * 1.15, 1e-3)
                    cam_obj.location = center - d * (radius * 3)
                cam_data.clip_start = radius * 0.01
                cam_data.clip_end = radius * 20
            path = os.path.join(outdir, f"{v}.png")
            rd.filepath = path
            bpy.ops.render.render(write_still=True)
            paths.append((v, path))
        sheet = filepath or os.path.join(outdir, "views.png")
        layout = _compose(paths, size, sheet)
        return {"filepath": sheet, "layout": layout, "engine": eng, "views": [p[0] for p in paths],
                "bounds": [list(mn), list(mx)]}
    finally:
        for o, m in wire_mods:
            o.modifiers.remove(m)
        for o in hidden:
            o.hide_render = False
        rd.engine = saved["engine"]
        rd.resolution_x, rd.resolution_y, rd.resolution_percentage = saved["res_x"], saved["res_y"], saved["pct"]
        scene.camera = saved["camera"]
        rd.film_transparent = saved["film"]
        rd.filepath = saved["filepath"]
        rd.image_settings.file_format = saved["fmt"]
        for k, v in saved_shading.items():
            try:
                setattr(shading, k, v)
            except Exception:
                pass
        if saved_eevee is not None:
            scene.eevee.taa_render_samples = saved_eevee
        if saved_cycles is not None:
            scene.cycles.samples = saved_cycles
        bpy.data.objects.remove(cam_obj)
        bpy.data.cameras.remove(cam_data)


def _compose(paths, size, out_path):
    import numpy as np
    n = len(paths)
    cols = 2 if n <= 4 else 3
    rows = math.ceil(n / cols)
    gap = 4
    W = cols * size + (cols - 1) * gap
    H = rows * size + (rows - 1) * gap
    sheet = np.full((H, W, 4), 0.08, dtype=np.float32)
    sheet[..., 3] = 1.0
    layout = []
    for i, (name, p) in enumerate(paths):
        img = bpy.data.images.load(p)
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)
        bpy.data.images.remove(img)
        rr, cc = divmod(i, cols)
        # Blender pixel rows go bottom->top; sheet built bottom-up too
        y0 = (rows - 1 - rr) * (size + gap) + (size - h) // 2
        x0 = cc * (size + gap) + (size - w) // 2
        sheet[y0:y0 + h, x0:x0 + w, :] = px
        layout.append(f"row{rr + 1} col{cc + 1}: {name}")
    out = bpy.data.images.new(".bp_sheet", W, H, alpha=True)
    out.pixels.foreach_set(sheet.ravel())
    out.filepath_raw = out_path
    out.file_format = "PNG"
    out.save()
    bpy.data.images.remove(out)
    return layout


def viewport_screenshot(filepath, max_size=640):
    ctx = find_view3d()
    if "area" not in ctx:
        raise RuntimeError("No 3D viewport (Blender is headless). Use render_views instead.")
    with bpy.context.temp_override(**ctx):
        bpy.ops.screen.screenshot_area(filepath=filepath)
    img = bpy.data.images.load(filepath)
    w, h = img.size
    if max(w, h) > max_size:
        s = max_size / max(w, h)
        img.scale(int(w * s), int(h * s))
        img.save()
        w, h = img.size
    bpy.data.images.remove(img)
    return {"filepath": filepath, "width": w, "height": h}
