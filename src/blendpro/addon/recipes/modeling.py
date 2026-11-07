"""Modeling, mesh cleanup & UV recipes (Blender 5.x, headless-safe)."""
import math
import random
from contextlib import contextmanager

import bmesh
import bpy
import mathutils
from mathutils import Matrix, Vector

from . import recipe
from ..util import find_view3d, get_obj, r

AXES = {"X": 0, "Y": 1, "Z": 2}


# ----------------------------------------------------------------- helpers
def _collection():
    c = getattr(bpy.context, "collection", None)
    return c if c is not None else bpy.context.scene.collection


def _link(obj):
    _collection().objects.link(obj)
    return obj


def _vec3(v, what="location"):
    try:
        t = tuple(float(x) for x in v)
    except Exception:
        raise ValueError(f"{what} must be 3 numbers, got {v!r}")
    if len(t) != 3:
        raise ValueError(f"{what} must be 3 numbers, got {v!r}")
    return t


def _axis(axis):
    a = str(axis).upper()
    if a not in AXES:
        raise ValueError(f"axis must be X, Y or Z, got {axis!r}")
    return a


def _mesh_obj(target):
    if not target:
        raise ValueError("target (object name) is required")
    o = get_obj(target)
    if o.type != "MESH":
        raise ValueError(f"{target!r} is a {o.type}, expected a MESH object")
    bpy.context.view_layer.update()  # matrix_world may be stale after scripted edits
    return o


def _bm_to_obj(bm, name, location=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = _link(bpy.data.objects.new(name, me))
    o.location = _vec3(location)
    return o


def _counts(me):
    ngons = sum(1 for p in me.polygons if p.loop_total > 4)
    tris = sum(p.loop_total - 2 for p in me.polygons)
    return {"verts": len(me.vertices), "edges": len(me.edges), "faces": len(me.polygons), "tris": tris, "ngons": ngons}


def _stats(obj, evaluated=True):
    """Counts + world dimensions (evaluated = with modifiers)."""
    bpy.context.view_layer.update()
    out = {"object": obj.name}
    if obj.type == "MESH":
        if evaluated and obj.modifiers:
            dg = bpy.context.evaluated_depsgraph_get()
            ev = obj.evaluated_get(dg)
            me = ev.to_mesh()
            out.update(_counts(me))
            ev.to_mesh_clear()
        else:
            out.update(_counts(obj.data))
    out["dimensions"] = r(list(obj.dimensions))
    out["location"] = r(list(obj.matrix_world.translation))
    if obj.modifiers:
        out["modifiers"] = [m.type for m in obj.modifiers]
    return out


def _apply_modifiers(obj):
    """Bake the whole modifier stack into the mesh (data API, no ops)."""
    if not obj.modifiers:
        return
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    new = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    old = obj.data
    new.name = old.name
    obj.modifiers.clear()
    obj.data = new
    if old.users == 0:
        bpy.data.meshes.remove(old)


def _ensure_object_mode():
    o = bpy.context.view_layer.objects.active
    if o is not None and o.mode != "OBJECT":
        with bpy.context.temp_override(**find_view3d(), active_object=o, object=o):
            bpy.ops.object.mode_set(mode="OBJECT")


@contextmanager
def _edit_mode(o, select_all=True):
    """Enter edit mode on exactly `o` (works headless + GUI); always returns to OBJECT."""
    _ensure_object_mode()
    if o.hide_get() or o.hide_viewport:
        raise ValueError(f"{o.name!r} is hidden; unhide it first")
    bpy.context.view_layer.objects.active = o
    ov = dict(find_view3d(), active_object=o, object=o, edit_object=o, selected_objects=[o], selected_editable_objects=[o])
    with bpy.context.temp_override(**ov):
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            if select_all:
                bpy.ops.mesh.select_all(action="SELECT")
            yield
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")


def _uv_bounds(me, layer=None):
    uv = layer or me.uv_layers.active
    if uv is None or not len(uv.data):
        return None
    us = [d.uv[0] for d in uv.data]
    vs = [d.uv[1] for d in uv.data]
    return r([min(us), min(vs), max(us), max(vs)])


def _ensure_uv(me, uv_name=None):
    if uv_name:
        uv = me.uv_layers.get(uv_name) or me.uv_layers.new(name=uv_name)
    else:
        uv = me.uv_layers.active or me.uv_layers.new(name="UVMap")
    me.uv_layers.active = uv
    return uv


def _single_user_data(o):
    if o.data.users > 1:
        o.data = o.data.copy()
        return True
    return False


# ------------------------------------------------------------ hard surface
@recipe("modeling", tags=("hard-surface", "primitive", "bevel"))
def rounded_box(name="RoundedBox", size=(1.0, 1.0, 1.0), bevel_width=0.05, segments=3,
                weighted_normals=True, apply=False, location=(0, 0, 0)) -> dict:
    '''Box with real dimensions (scale stays 1) + Bevel modifier + Weighted Normal for clean hard-surface shading.
    size in metres; bevel_width is clamped to < half the smallest side. apply=True bakes the modifiers.'''
    sx, sy, sz = _vec3(size, "size")
    if min(sx, sy, sz) <= 0:
        raise ValueError("size components must be > 0")
    bevel_width = min(float(bevel_width), min(sx, sy, sz) * 0.499)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((sx, sy, sz, 1)))
    o = _bm_to_obj(bm, name, location)
    o.data.shade_smooth()
    if bevel_width > 0:
        b = o.modifiers.new("Bevel", "BEVEL")
        b.width = bevel_width
        b.segments = max(1, int(segments))
        b.limit_method = "ANGLE"
        b.miter_outer = "MITER_ARC"
    if weighted_normals:
        w = o.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
        w.keep_sharp = True
    if apply:
        _apply_modifiers(o)
    return _stats(o)


@recipe("modeling", tags=("hard-surface", "boolean", "cleanup"))
def boolean_cut(target, cutter=None, operation="DIFFERENCE", cutter_size=(0.5, 0.5, 0.5),
                cutter_location=None, solver="EXACT", apply=True, delete_cutter=True, cleanup=True) -> dict:
    '''Boolean DIFFERENCE/UNION/INTERSECT of `target` with a cutter object, then merge/degenerate cleanup.
    cutter=None creates a box cutter (cutter_size, cutter_location in world space; default = target origin).
    apply=False keeps a live modifier and hides the cutter (display WIRE).'''
    o = _mesh_obj(target)
    operation = operation.upper()
    if operation not in {"DIFFERENCE", "UNION", "INTERSECT"}:
        raise ValueError("operation must be DIFFERENCE, UNION or INTERSECT")
    solvers = bpy.types.BooleanModifier.bl_rna.properties["solver"].enum_items.keys()
    if solver.upper() not in solvers:
        raise ValueError(f"solver must be one of {solvers}")
    created = False
    if cutter is None:
        sx, sy, sz = _vec3(cutter_size, "cutter_size")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((sx, sy, sz, 1)))
        loc = cutter_location if cutter_location is not None else o.matrix_world.translation
        c = _bm_to_obj(bm, f"{o.name}_Cutter", loc)
        created = True
    else:
        c = _mesh_obj(cutter)
    before = len(o.data.vertices)
    _single_user_data(o)
    m = o.modifiers.new("Boolean", "BOOLEAN")
    m.object = c
    m.operation = operation
    m.solver = solver.upper()
    result = {"cutter": c.name, "operation": operation}
    if apply:
        # bake only this modifier: move it to the end, evaluate, keep others live
        others = [(md.name, md.show_viewport) for md in o.modifiers if md != m]
        for md in o.modifiers:
            if md != m:
                md.show_viewport = False
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        new = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
        old = o.data
        o.modifiers.remove(m)
        for nm, vis in others:
            o.modifiers[nm].show_viewport = vis
        new.name = old.name
        o.data = new
        if old.users == 0:
            bpy.data.meshes.remove(old)
        if cleanup:
            bm = bmesh.new()
            bm.from_mesh(o.data)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
            bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-5)
            bm.to_mesh(o.data)
            bm.free()
        if delete_cutter:
            bpy.data.objects.remove(c)
            result["cutter"] = None
    else:
        c.display_type = "WIRE"
        c.hide_render = True
    result.update(_stats(o))
    result["verts_before"] = before
    result["cutter_created"] = created
    return result


@recipe("modeling", tags=("curve", "pipe", "cable"))
def pipe_along_points(name="Pipe", points=((0, 0, 0), (1, 0, 0), (1, 1, 0.5)), radius=0.05,
                      bevel_resolution=4, smooth=False, cap_ends=True, to_mesh=True, location=(0, 0, 0)) -> dict:
    '''Tube/pipe/cable through a list of 3D points (local to location). smooth=True uses a smooth NURBS path,
    False a sharp polyline. to_mesh=True converts to a mesh object (curve is removed).'''
    pts = [_vec3(p, "point") for p in points]
    if len(pts) < 2:
        raise ValueError("points needs at least 2 points")
    if radius <= 0:
        raise ValueError("radius must be > 0")
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = float(radius)
    cu.bevel_resolution = int(bevel_resolution)
    cu.use_fill_caps = bool(cap_ends)
    cu.resolution_u = 12
    sp = cu.splines.new("NURBS" if smooth else "POLY")
    sp.points.add(len(pts) - 1)
    for p, c in zip(sp.points, pts):
        p.co = (*c, 1.0)
    if smooth:
        sp.use_endpoint_u = True
        sp.order_u = min(4, len(pts))
    co = _link(bpy.data.objects.new(name, cu))
    co.location = _vec3(location)
    length = sum((Vector(pts[i + 1]) - Vector(pts[i])).length for i in range(len(pts) - 1))
    if to_mesh:
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(co.evaluated_get(dg), depsgraph=dg)
        mw = co.matrix_world.copy()
        bpy.data.objects.remove(co)
        bpy.data.curves.remove(cu)
        mo = _link(bpy.data.objects.new(name, me))
        mo.matrix_world = mw
        out = _stats(mo)
    else:
        out = _stats(co)
    out["polyline_length"] = r(length)
    return out


@recipe("modeling", tags=("array", "radial", "hard-surface"))
def radial_array(target, count=8, axis="Z", radius=0.0, apply=False) -> dict:
    '''Radial (circular) array of `target` around its own origin using an Array modifier + rotated pivot Empty.
    radius>0 first shifts the mesh outward (along X, or Y when axis=X) so copies orbit the origin.'''
    o = _mesh_obj(target)
    count = int(count)
    if count < 2:
        raise ValueError("count must be >= 2")
    a = _axis(axis)
    if radius:
        _single_user_data(o)
        d = [0.0, 0.0, 0.0]
        d[1 if a == "X" else 0] = float(radius)
        o.data.transform(Matrix.Translation(d))
    e = _link(bpy.data.objects.new(f"{o.name}_RadialPivot", None))
    e.empty_display_type = "PLAIN_AXES"
    e.empty_display_size = 0.25
    e.parent = o
    rot = [0.0, 0.0, 0.0]
    rot[AXES[a]] = 2 * math.pi / count
    e.rotation_euler = rot
    m = o.modifiers.new("RadialArray", "ARRAY")
    m.count = count
    m.use_relative_offset = False
    m.use_object_offset = True
    m.offset_object = e
    out = {"pivot": e.name, "count": count}
    if apply:
        _apply_modifiers(o)
        bpy.data.objects.remove(e)
        out["pivot"] = None
    out.update(_stats(o))
    return out


@recipe("modeling", tags=("array", "curve", "deform"))
def array_along_curve(target, points=((0, 0, 0), (2, 1, 0), (4, 0, 0)), curve=None, count=None,
                      axis="X", gap=0.0, merge=False, apply=False) -> dict:
    '''Repeat `target` along a curve (Array + Curve modifiers): chains, fences, rails, tank treads.
    curve=None builds a smooth path from points (world space). count=None fits copies to the curve length.
    target is moved onto the curve origin; its local +axis is the repeat/deform direction.'''
    o = _mesh_obj(target)
    a = _axis(axis)
    if curve:
        c = get_obj(curve)
        if c.type != "CURVE":
            raise ValueError(f"{curve!r} is not a CURVE object")
    else:
        pts = [_vec3(p, "point") for p in points]
        if len(pts) < 2:
            raise ValueError("points needs at least 2 points")
        cu = bpy.data.curves.new(f"{o.name}_Path", "CURVE")
        cu.dimensions = "3D"
        sp = cu.splines.new("NURBS")
        sp.points.add(len(pts) - 1)
        for p, v in zip(sp.points, pts):
            p.co = (*v, 1.0)
        sp.use_endpoint_u = True
        sp.order_u = min(4, len(pts))
        c = _link(bpy.data.objects.new(f"{o.name}_Path", cu))
    o.location = c.matrix_world.translation
    m = o.modifiers.new("CurveArray", "ARRAY")
    if count is None:
        m.fit_type = "FIT_CURVE"
        m.curve = c
    else:
        if int(count) < 1:
            raise ValueError("count must be >= 1")
        m.fit_type = "FIXED_COUNT"
        m.count = int(count)
    off = [0.0, 0.0, 0.0]
    off[AXES[a]] = 1.0
    m.relative_offset_displace = off
    if gap:
        m.use_constant_offset = True
        cd = [0.0, 0.0, 0.0]
        cd[AXES[a]] = float(gap)
        m.constant_offset_displace = cd
    m.use_merge_vertices = bool(merge)
    cm = o.modifiers.new("Curve", "CURVE")
    cm.object = c
    cm.deform_axis = "POS_" + a
    if apply:
        _apply_modifiers(o)
    out = {"curve": c.name}
    out.update(_stats(o))
    return out


@recipe("modeling", tags=("mirror", "symmetry", "hard-surface"))
def mirror_setup(target, axis="X", bisect=True, clipping=True, merge=True, mirror_object=None, apply=False) -> dict:
    '''Symmetric modeling: Mirror modifier with bisect (cuts away the -side), clipping and merge.
    axis can be "X", "XY", "XYZ"... mirror_object mirrors across another object's origin.'''
    o = _mesh_obj(target)
    axes = str(axis).upper()
    if not axes or any(ch not in AXES for ch in axes):
        raise ValueError("axis must be a combination of X, Y, Z (e.g. 'X' or 'XY')")
    m = o.modifiers.new("Mirror", "MIRROR")
    m.use_axis = [ch in axes for ch in "XYZ"]
    m.use_bisect_axis = [bisect and ch in axes for ch in "XYZ"]
    m.use_clip = bool(clipping)
    m.use_mirror_merge = bool(merge)
    if mirror_object:
        m.mirror_object = get_obj(mirror_object)
    if apply:
        _apply_modifiers(o)
    return _stats(o)


@recipe("modeling", tags=("lathe", "spin", "revolve", "primitive"))
def lathe_profile(name="Lathe", profile=((0.0, 0.0), (0.3, 0.0), (0.35, 0.1), (0.2, 0.5), (0.25, 0.8), (0.0, 0.8)),
                  steps=32, angle=360.0, location=(0, 0, 0)) -> dict:
    '''Revolve a 2D (radius, height) profile around Z: vases, bottles, columns, wheels, lamp shades.
    Points with radius 0 become merged poles. angle<360 makes an open sweep.'''
    prof = [(float(p[0]), float(p[1])) for p in profile]
    if len(prof) < 2:
        raise ValueError("profile needs >= 2 (radius, z) points")
    if any(p[0] < 0 for p in prof):
        raise ValueError("profile radii must be >= 0")
    steps = int(steps)
    if steps < 3:
        raise ValueError("steps must be >= 3")
    bm = bmesh.new()
    vs = [bm.verts.new((rr, 0.0, z)) for rr, z in prof]
    es = [bm.edges.new((vs[i], vs[i + 1])) for i in range(len(vs) - 1)]
    full = abs(float(angle)) >= 359.999
    bmesh.ops.spin(bm, geom=vs + es, cent=(0, 0, 0), axis=(0, 0, 1), angle=math.radians(float(angle)),
                   steps=steps, use_merge=full, use_duplicate=False)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = _bm_to_obj(bm, name, location)
    o.data.shade_smooth()
    o.data.set_sharp_from_angle(angle=math.radians(40))
    return _stats(o)


@recipe("modeling", tags=("text", "font", "signage"))
def text_3d(text="Text", name=None, size=1.0, extrude=0.05, bevel=0.0, align="CENTER",
            font_path=None, to_mesh=True, location=(0, 0, 0)) -> dict:
    '''3D text (extruded, optional bevel), centred on its origin; to_mesh=True converts to a mesh for export.
    font_path loads a .ttf/.otf (default Blender font otherwise). Text lies on XY, facing +Z.'''
    if not text:
        raise ValueError("text must be non-empty")
    name = name or f"Text_{text[:12]}"
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = str(text)
    cu.size = float(size)
    cu.extrude = float(extrude)
    cu.bevel_depth = float(bevel)
    al = align.upper()
    if al not in {"LEFT", "CENTER", "RIGHT", "JUSTIFY", "FLUSH"}:
        raise ValueError("align must be LEFT, CENTER, RIGHT, JUSTIFY or FLUSH")
    cu.align_x = al
    cu.align_y = "CENTER"
    if font_path:
        cu.font = bpy.data.fonts.load(font_path, check_existing=True)
    co = _link(bpy.data.objects.new(name, cu))
    co.location = _vec3(location)
    if not to_mesh:
        return _stats(co)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(co.evaluated_get(dg), depsgraph=dg)
    mw = co.matrix_world.copy()
    bpy.data.objects.remove(co)
    bpy.data.curves.remove(cu)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bm.to_mesh(me)
    bm.free()
    mo = _link(bpy.data.objects.new(name, me))
    mo.matrix_world = mw
    return _stats(mo)


@recipe("modeling", tags=("organic", "low-poly", "environment", "rock"))
def low_poly_rock(name="Rock", size=1.0, seed=0, subdivisions=3, roughness=0.35, flatten=0.7,
                  location=(0, 0, 0)) -> dict:
    '''Faceted low-poly rock/boulder: noise-displaced icosphere, flattened, flat shaded, sitting on z=0.
    seed varies the shape; subdivisions 1-5 (20, 80, 320, 1280, 5120 tris); roughness 0..1; flatten scales height.'''
    subdivisions = int(subdivisions)
    if not 1 <= subdivisions <= 5:
        raise ValueError("subdivisions must be 1..5")
    rng = random.Random(seed)
    off = Vector((rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(-100, 100)))
    stretch = Vector((rng.uniform(0.8, 1.25), rng.uniform(0.8, 1.25), 1.0))
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=0.5)
    for v in bm.verts:
        n = mathutils.noise.noise(v.co * 2.2 + off)
        n2 = mathutils.noise.noise(v.co * 5.0 + off * 0.5)
        k = 1.0 + float(roughness) * (0.8 * n + 0.35 * n2)
        v.co = Vector((v.co.x * stretch.x, v.co.y * stretch.y, v.co.z * float(flatten))) * k * float(size)
    zmin = min(v.co.z for v in bm.verts)
    for v in bm.verts:
        v.co.z -= zmin
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = _bm_to_obj(bm, name, location)
    return _stats(o)


@recipe("modeling", tags=("architecture", "blockout", "stairs"))
def stairs(name="Stairs", steps=10, width=1.0, step_rise=0.18, step_run=0.28, location=(0, 0, 0)) -> dict:
    '''Solid straight staircase (one closed mesh) climbing +Y, width along X centred on origin, base at z=0.
    Building-code defaults: rise 0.18 m, run 0.28 m. Side faces are quads (no ngons).'''
    n = int(steps)
    if n < 1 or width <= 0 or step_rise <= 0 or step_run <= 0:
        raise ValueError("steps >= 1 and width/step_rise/step_run > 0 required")
    bm = bmesh.new()
    hw = width / 2
    V = {}

    def v(side, j, k):
        key = (side, j, k)
        if key not in V:
            V[key] = bm.verts.new((hw if side else -hw, j * step_run, k * step_rise))
        return V[key]

    def quad(*vs):
        bm.faces.new(vs)

    for i in range(n):
        for s in (0, 1):  # side walls as a grid of quads (no ngons, no T-junctions)
            for k in range(i + 1):
                quad(v(s, i, k), v(s, i + 1, k), v(s, i + 1, k + 1), v(s, i, k + 1))
        quad(v(0, i, 0), v(0, i + 1, 0), v(1, i + 1, 0), v(1, i, 0))                  # bottom
        quad(v(0, i, i), v(1, i, i), v(1, i, i + 1), v(0, i, i + 1))                  # riser
        quad(v(0, i, i + 1), v(1, i, i + 1), v(1, i + 1, i + 1), v(0, i + 1, i + 1))  # tread
        quad(v(0, n, i), v(0, n, i + 1), v(1, n, i + 1), v(1, n, i))                  # back
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    nonman = sum(1 for e in bm.edges if not e.is_manifold)
    o = _bm_to_obj(bm, name, location)
    out = _stats(o)
    out["total_rise"] = r(n * step_rise)
    out["total_run"] = r(n * step_run)
    out["non_manifold_edges"] = nonman
    return out


# ----------------------------------------------------------------- cleanup
@recipe("modeling", tags=("cleanup", "merge", "normals", "import"))
def mesh_cleanup(target, merge_distance=0.0001, dissolve_degenerate=True, delete_loose=True,
                 recalc_normals=True, fill_holes_sides=0) -> dict:
    '''One-shot cleanup for imported/boolean/scan meshes: merge by distance, dissolve degenerate,
    delete loose verts/edges, recalculate outside normals, optionally fill holes up to N sides. Reports deltas.'''
    o = _mesh_obj(target)
    _single_user_data(o)
    before = _counts(o.data)
    bm = bmesh.new()
    bm.from_mesh(o.data)
    rep = {}
    nv = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=float(merge_distance))
    rep["merged_verts"] = nv - len(bm.verts)
    if dissolve_degenerate:
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=float(merge_distance))
    if delete_loose:
        loose_e = [e for e in bm.edges if not e.link_faces]
        if loose_e:
            bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
        loose_v = [v for v in bm.verts if not v.link_edges]
        if loose_v:
            bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
        rep["loose_removed"] = len(loose_e) + len(loose_v)
    if fill_holes_sides:
        bmesh.ops.holes_fill(bm, edges=bm.edges, sides=int(fill_holes_sides))
    if recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    nonmanifold = sum(1 for e in bm.edges if not e.is_manifold)
    bm.to_mesh(o.data)
    bm.free()
    o.data.update()
    after = _counts(o.data)
    return {"object": o.name, "before": before, "after": after, "non_manifold_edges": nonmanifold, **rep}


@recipe("modeling", tags=("cleanup", "ngons", "triangulate", "quads", "export"))
def fix_ngons(target, mode="TRIANGULATE", ngon_method="BEAUTY", max_angle=40.0) -> dict:
    '''Remove ngons (faces with >4 sides): mode TRIANGULATE splits only ngons into tris (game export safe);
    QUADS triangulates ngons then rejoins tris into quads where possible (max_angle in degrees).'''
    o = _mesh_obj(target)
    mode = mode.upper()
    if mode not in {"TRIANGULATE", "QUADS"}:
        raise ValueError("mode must be TRIANGULATE or QUADS")
    _single_user_data(o)
    before = _counts(o.data)
    bm = bmesh.new()
    bm.from_mesh(o.data)
    ngons = [f for f in bm.faces if len(f.verts) > 4]
    res = bmesh.ops.triangulate(bm, faces=ngons, ngon_method=ngon_method.upper(), quad_method="BEAUTY")
    if mode == "QUADS":
        a = math.radians(float(max_angle))
        bmesh.ops.join_triangles(bm, faces=res["faces"], angle_face_threshold=a, angle_shape_threshold=a)
    bm.to_mesh(o.data)
    bm.free()
    return {"object": o.name, "before": before, "after": _counts(o.data)}


@recipe("modeling", tags=("cleanup", "transform", "export"))
def apply_transforms(target, location=False, rotation=True, scale=True, make_single_user=True) -> dict:
    '''Apply (freeze) rotation/scale (and optionally location) into mesh/curve data without bpy.ops.
    Children keep their world placement. Shared mesh data is copied first (make_single_user) or raises.'''
    o = get_obj(target)
    if o.type not in {"MESH", "CURVE"}:
        raise ValueError(f"{target!r} is {o.type}; only MESH/CURVE data can be transformed")
    if o.data.users > 1:
        if not make_single_user:
            raise ValueError(f"{o.data.name!r} is shared by {o.data.users} objects; pass make_single_user=True")
        _single_user_data(o)
    bpy.context.view_layer.update()
    B = o.matrix_basis.copy()
    loc, rot, scl = B.decompose()
    T = Matrix.Translation(Vector((0, 0, 0)) if location else loc)
    R = Matrix.Identity(4) if rotation else rot.to_matrix().to_4x4()
    S = Matrix.Identity(4) if scale else Matrix.Diagonal((*scl, 1))
    Bn = T @ R @ S
    M = Bn.inverted() @ B
    o.data.transform(M)
    o.data.update()
    keep_mode = o.rotation_mode
    o.matrix_basis = Bn
    o.rotation_mode = keep_mode
    for ch in o.children:
        ch.matrix_parent_inverse = M @ ch.matrix_parent_inverse
    out = _stats(o, evaluated=False)
    out.update({"rotation_deg": r([math.degrees(a) for a in o.rotation_euler], 2), "scale": r(list(o.scale)),
                "children_fixed": len(o.children)})
    return out


@recipe("modeling", tags=("cleanup", "origin", "pivot"))
def set_origin(target, mode="BOTTOM_CENTER", point=None) -> dict:
    '''Move the object origin without moving the geometry. mode: BOTTOM_CENTER (floor placement),
    BOUNDS_CENTER, GEOMETRY (vertex median), CURSOR, WORLD_ORIGIN, POINT (world `point`). Children stay put.'''
    o = _mesh_obj(target)
    mode = mode.upper()
    me = o.data
    if not len(me.vertices) and mode in {"BOTTOM_CENTER", "BOUNDS_CENTER", "GEOMETRY"}:
        raise ValueError("mesh has no vertices")
    mw = o.matrix_world.copy()
    if mode in {"BOTTOM_CENTER", "BOUNDS_CENTER"}:
        # bounds in world space so "bottom" means world -Z even for rotated objects
        ws = [mw @ v.co for v in me.vertices]
        mn = Vector([min(c[i] for c in ws) for i in range(3)])
        mx = Vector([max(c[i] for c in ws) for i in range(3)])
        pw = (mn + mx) / 2
        if mode == "BOTTOM_CENTER":
            pw.z = mn.z
        p = mw.inverted() @ pw
    elif mode == "GEOMETRY":
        p = sum((v.co for v in me.vertices), Vector()) / len(me.vertices)
    elif mode == "CURSOR":
        p = mw.inverted() @ bpy.context.scene.cursor.location
    elif mode == "WORLD_ORIGIN":
        p = mw.inverted() @ Vector((0, 0, 0))
    elif mode == "POINT":
        if point is None:
            raise ValueError("mode POINT needs point=(x,y,z)")
        p = mw.inverted() @ Vector(_vec3(point, "point"))
    else:
        raise ValueError("mode must be BOTTOM_CENTER, BOUNDS_CENTER, GEOMETRY, CURSOR, WORLD_ORIGIN or POINT")
    _single_user_data(o)
    me = o.data
    me.transform(Matrix.Translation(-p))
    me.update()
    nw = mw.copy()
    nw.translation = mw @ p
    o.matrix_world = nw
    for ch in o.children:
        ch.matrix_parent_inverse = Matrix.Translation(-p) @ ch.matrix_parent_inverse
    return _stats(o, evaluated=False)


@recipe("modeling", tags=("cleanup", "decimate", "lod", "optimization", "xr"))
def decimate_to_tris(target, target_tris=5000, apply=True, symmetry_axis=None) -> dict:
    '''Reduce a mesh to roughly target_tris triangles (Decimate COLLAPSE, ratio computed from current count).
    Use for LODs / XR budgets. symmetry_axis "X"/"Y"/"Z" keeps symmetry. No-op if already under budget.'''
    o = _mesh_obj(target)
    cur = _counts(o.data)["tris"]
    tt = int(target_tris)
    if tt < 4:
        raise ValueError("target_tris must be >= 4")
    if cur <= tt:
        return {"object": o.name, "tris_before": cur, "tris_after": cur, "ratio": 1.0, "note": "already under budget"}
    ratio = tt / cur
    m = o.modifiers.new("Decimate", "DECIMATE")
    m.decimate_type = "COLLAPSE"
    m.ratio = ratio
    m.use_collapse_triangulate = True
    if symmetry_axis:
        m.use_symmetry = True
        m.symmetry_axis = _axis(symmetry_axis)
    if apply:
        _single_user_data(o)
        _apply_modifiers(o)
    st = _stats(o)
    return {"object": o.name, "tris_before": cur, "tris_after": st["tris"], "ratio": r(ratio, 4),
            "applied": bool(apply), "dimensions": st["dimensions"]}


@recipe("modeling", tags=("cleanup", "remesh", "sculpt", "retopo"))
def remesh_voxel(target, voxel_size=0.05, adaptivity=0.0, smooth_shade=True, apply=True, max_voxels=4e8) -> dict:
    '''Voxel remesh into uniform, manifold quads (fixes non-manifold/intersecting scans or kitbash before sculpting).
    voxel_size in metres; refuses sizes that would create > max_voxels voxels. UVs are lost.'''
    o = _mesh_obj(target)
    vs = float(voxel_size)
    if vs <= 0:
        raise ValueError("voxel_size must be > 0")
    d = o.dimensions
    est = max(d.x, vs) * max(d.y, vs) * max(d.z, vs) / vs ** 3
    if est > max_voxels:
        raise ValueError(f"voxel_size {vs} would make ~{est:.2e} voxels; use a larger size (>= {r((d.x * d.y * d.z / max_voxels) ** (1/3), 4)})")
    before = _counts(o.data)["verts"]
    m = o.modifiers.new("Remesh", "REMESH")
    m.mode = "VOXEL"
    m.voxel_size = vs
    m.adaptivity = float(adaptivity)
    m.use_smooth_shade = bool(smooth_shade)
    if apply:
        _single_user_data(o)
        _apply_modifiers(o)
    out = _stats(o)
    out["verts_before"] = before
    return out


@recipe("modeling", tags=("cleanup", "separate", "split"))
def separate_parts(target, by="LOOSE") -> dict:
    '''Split one mesh object into several: by LOOSE (connected parts) or MATERIAL. Returns new object names.
    Origins stay at the original origin; follow with set_origin per part if needed.'''
    o = _mesh_obj(target)
    by = by.upper()
    if by not in {"LOOSE", "MATERIAL"}:
        raise ValueError("by must be LOOSE or MATERIAL")
    existing = set(bpy.data.objects.keys())
    _single_user_data(o)
    with _edit_mode(o):
        bpy.ops.mesh.separate(type=by)
    new = [n for n in bpy.data.objects.keys() if n not in existing]
    parts = [o.name] + new
    return {"objects": parts, "count": len(parts),
            "verts": {n: len(bpy.data.objects[n].data.vertices) for n in parts}}


@recipe("modeling", tags=("cleanup", "join", "merge"))
def join_objects(targets, name=None, merge_distance=0.0) -> dict:
    '''Join several mesh objects into one (first object keeps its origin/transform), optional rename and
    merge-by-distance of the seams. All targets must be MESH objects.'''
    if not targets or len(targets) < 2:
        raise ValueError("targets needs >= 2 object names")
    objs = [_mesh_obj(t) for t in targets]
    base = objs[0]
    _ensure_object_mode()
    bpy.context.view_layer.objects.active = base
    with bpy.context.temp_override(**find_view3d(), active_object=base, object=base,
                                   selected_objects=objs, selected_editable_objects=objs):
        bpy.ops.object.join()
    if name:
        base.name = name
        base.data.name = name
    if merge_distance:
        bm = bmesh.new()
        bm.from_mesh(base.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=float(merge_distance))
        bm.to_mesh(base.data)
        bm.free()
    return _stats(base)


@recipe("modeling", tags=("shading", "normals", "auto-smooth"))
def smooth_by_angle(target, angle=30.0, weighted_normals=False) -> dict:
    '''Blender 5.x "auto smooth": shade smooth + mark edges sharper than `angle` (degrees) as sharp
    (data API, replaces mesh.use_auto_smooth). weighted_normals=True adds a Weighted Normal modifier (hard-surface).'''
    o = _mesh_obj(target)
    if not 0 <= float(angle) <= 180:
        raise ValueError("angle must be 0..180 degrees")
    me = o.data
    me.shade_smooth()
    me.set_sharp_from_angle(angle=math.radians(float(angle)))
    attr = me.attributes.get("sharp_edge")
    sharp = 0
    if attr:
        vals = [False] * len(me.edges)
        attr.data.foreach_get("value", vals)
        sharp = sum(vals)
    if weighted_normals and not any(m.type == "WEIGHTED_NORMAL" for m in o.modifiers):
        w = o.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
        w.keep_sharp = True
    out = _stats(o)
    out["sharp_edges"] = sharp
    out["smooth_faces"] = sum(1 for p in me.polygons if p.use_smooth)
    return out


@recipe("modeling", tags=("hard-surface", "bevel", "edges"))
def bevel_weight_by_angle(target, angle=30.0, weight=1.0, add_bevel_modifier=True, width=0.02,
                          segments=3, mark_sharp=True) -> dict:
    '''Set bevel weight on edges whose face angle exceeds `angle` (deg) and add a weight-limited Bevel modifier,
    so only hard edges get rounded. Uses the 5.x "bevel_weight_edge" attribute.'''
    o = _mesh_obj(target)
    _single_user_data(o)
    a = math.radians(float(angle))
    bm = bmesh.new()
    bm.from_mesh(o.data)
    lay = bm.edges.layers.float.get("bevel_weight_edge") or bm.edges.layers.float.new("bevel_weight_edge")
    n = 0
    for e in bm.edges:
        if len(e.link_faces) == 2 and e.calc_face_angle(0.0) > a:
            e[lay] = float(weight)
            if mark_sharp:
                e.smooth = False
            n += 1
    bm.to_mesh(o.data)
    bm.free()
    if add_bevel_modifier:
        b = o.modifiers.new("Bevel", "BEVEL")
        b.limit_method = "WEIGHT"
        b.width = float(width)
        b.segments = int(segments)
        b.miter_outer = "MITER_ARC"
        b.harden_normals = False
    out = _stats(o)
    out["weighted_edges"] = n
    return out


# ---------------------------------------------------------------------- UV
@recipe("modeling", tags=("uv", "unwrap", "smart-project"))
def smart_uv_unwrap(target, method="SMART", angle=66.0, margin=0.02, uv_name=None, pack=True) -> dict:
    '''Unwrap a mesh: SMART (smart project, good default for hard-surface), CUBE (box mapping), UNWRAP
    (angle-based using existing seams) or LIGHTMAP (non-overlapping). angle in degrees (SMART); margin 0..1.'''
    o = _mesh_obj(target)
    method = method.upper()
    if method not in {"SMART", "CUBE", "UNWRAP", "LIGHTMAP"}:
        raise ValueError("method must be SMART, CUBE, UNWRAP or LIGHTMAP")
    if not len(o.data.polygons):
        raise ValueError(f"{target!r} has no faces to unwrap")
    uv = _ensure_uv(o.data, uv_name)
    uvn = uv.name
    with _edit_mode(o):
        if method == "SMART":
            bpy.ops.uv.smart_project(angle_limit=math.radians(float(angle)), island_margin=float(margin))
        elif method == "CUBE":
            bpy.ops.uv.cube_project(cube_size=max(o.dimensions) or 1.0)
        elif method == "UNWRAP":
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=float(margin))
        else:
            bpy.ops.uv.lightmap_pack(PREF_CONTEXT="ALL_FACES", PREF_MARGIN_DIV=max(0.01, float(margin) * 10))
        if pack and method in {"CUBE", "UNWRAP"}:
            bpy.ops.uv.pack_islands(margin=float(margin), rotate=True)
    me = o.data
    return {"object": o.name, "uv_layer": uvn, "uv_layers": me.uv_layers.keys(), "method": method,
            "uv_bounds": _uv_bounds(me, me.uv_layers[uvn])}


@recipe("modeling", tags=("shell", "thickness", "hard-surface"))
def solidify_shell(target, thickness=0.02, offset=-1.0, even_thickness=True, rim=True, apply=False) -> dict:
    """Give a surface real thickness with a Solidify modifier (walls, cloth, armor, leaves that must cast shadows).
    offset -1 grows inward (keeps outer silhouette), +1 outward. apply=True bakes it. Apply scale first for even walls."""
    o = _mesh_obj(target)
    if thickness == 0:
        raise ValueError("thickness must be non-zero")
    m = next((x for x in o.modifiers if x.type == "SOLIDIFY"), None) or o.modifiers.new("Solidify", "SOLIDIFY")
    m.thickness = float(thickness)
    m.offset = max(-1.0, min(1.0, float(offset)))
    m.use_even_offset = bool(even_thickness)
    m.use_rim = bool(rim)
    if apply:
        _apply_modifiers(o)
    out = _stats(o)
    out["applied"] = bool(apply)
    return out


@recipe("modeling", tags=("uv", "seams", "unwrap"))
def seams_by_angle_unwrap(target, angle=45.0, clear_existing=True, method="ANGLE_BASED", margin=0.02) -> dict:
    '''Mark UV seams on edges sharper than `angle` (deg) + open boundaries, then unwrap and pack.
    Cleaner islands than smart project for mechanical parts; method ANGLE_BASED/CONFORMAL/MINIMUM_STRETCH.'''
    o = _mesh_obj(target)
    methods = bpy.ops.uv.unwrap.get_rna_type().properties["method"].enum_items.keys()
    if method.upper() not in methods:
        raise ValueError(f"method must be one of {list(methods)}")
    _single_user_data(o)
    a = math.radians(float(angle))
    bm = bmesh.new()
    bm.from_mesh(o.data)
    n = 0
    for e in bm.edges:
        if clear_existing:
            e.seam = False
        if len(e.link_faces) != 2 or e.calc_face_angle(0.0) > a:
            e.seam = True
        n += e.seam
    bm.to_mesh(o.data)
    bm.free()
    uvn = _ensure_uv(o.data).name  # RNA refs go stale across edit mode
    with _edit_mode(o):
        bpy.ops.uv.unwrap(method=method.upper(), margin=float(margin))
        bpy.ops.uv.pack_islands(margin=float(margin), rotate=True)
    me = o.data
    return {"object": o.name, "seams": n, "uv_layer": uvn, "uv_bounds": _uv_bounds(me)}


@recipe("modeling", tags=("symmetry", "mirror", "cleanup", "sculpt"))
def symmetrize(target, axis="X", direction="NEGATIVE_TO_POSITIVE", merge_threshold=0.0001) -> dict:
    """Make a mesh exactly symmetric by copying one half onto the other (Mesh > Symmetrize, via bmesh).
    direction NEGATIVE_TO_POSITIVE keeps the -axis half and overwrites +axis. Use after one-sided cleanup or
    sculpting; mesh_analysis x_symmetry should read 1.0 afterwards."""
    o = _mesh_obj(target)
    a = _axis(axis)
    if direction not in ("NEGATIVE_TO_POSITIVE", "POSITIVE_TO_NEGATIVE"):
        raise ValueError("direction must be NEGATIVE_TO_POSITIVE or POSITIVE_TO_NEGATIVE")
    _single_user_data(o)
    me = o.data
    bm = bmesh.new()
    bm.from_mesh(me)
    flag = ("-" if direction == "NEGATIVE_TO_POSITIVE" else "") + a
    bmesh.ops.symmetrize(bm, input=bm.verts[:] + bm.edges[:] + bm.faces[:], direction=flag,
                         dist=float(merge_threshold))
    bm.to_mesh(me)
    bm.free()
    me.update()
    idx = "XYZ".index(a)
    cos = [v.co[idx] for v in me.vertices]
    out = _stats(o, evaluated=False)
    out["axis"] = a
    out["extent_on_axis"] = r([min(cos), max(cos)]) if cos else None
    return out


@recipe("modeling", tags=("uv", "texel-density", "xr", "optimization"))
def texel_density(target, texture_size=1024, target_density=None, uv_name=None) -> dict:
    '''Report texel density (pixels per metre, world scale included) of a mesh's UVs for a given texture size;
    target_density (px/m) rescales the UVs around their centre to hit it (may then exceed 0-1 → re-pack/tile).'''
    o = _mesh_obj(target)
    me = o.data
    uv = me.uv_layers.get(uv_name) if uv_name else me.uv_layers.active
    if uv is None:
        raise ValueError(f"{target!r} has no UV layer{' ' + repr(uv_name) if uv_name else ''}; unwrap first")
    tex = int(texture_size)
    if tex <= 0:
        raise ValueError("texture_size must be > 0")

    def measure():
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.transform(o.matrix_world)
        lay = bm.loops.layers.uv[uv.name]
        a3 = a2 = 0.0
        for f in bm.faces:
            a3 += f.calc_area()
            pts = [l[lay].uv for l in f.loops]
            s = 0.0
            for i in range(len(pts)):
                p, q = pts[i], pts[(i + 1) % len(pts)]
                s += p.x * q.y - q.x * p.y
            a2 += abs(s) / 2
        bm.free()
        return a3, a2

    a3, a2 = measure()
    if a3 <= 0 or a2 <= 0:
        raise ValueError("zero surface or UV area; cannot compute texel density")
    dens = math.sqrt(a2 / a3) * tex
    out = {"object": o.name, "uv_layer": uv.name, "texture_size": tex, "surface_m2": r(a3, 4),
           "uv_area": r(a2, 4), "density_px_per_m": r(dens, 2)}
    if target_density:
        k = float(target_density) / dens
        n = len(uv.data)
        co = [0.0] * (2 * n)
        uv.data.foreach_get("uv", co)
        us, vs = co[0::2], co[1::2]
        cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
        for i in range(n):
            co[2 * i] = cu + (co[2 * i] - cu) * k
            co[2 * i + 1] = cv + (co[2 * i + 1] - cv) * k
        uv.data.foreach_set("uv", co)
        me.update()
        a3, a2 = measure()
        out["scale_factor"] = r(k, 4)
        out["density_after"] = r(math.sqrt(a2 / a3) * tex, 2)
        out["uv_bounds"] = _uv_bounds(me, uv)
    return out
