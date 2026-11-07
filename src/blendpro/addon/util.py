"""Shared helpers for BlendPro handlers (runs inside Blender)."""
import math
import bpy
import mathutils

ID_COLLECTIONS = {
    "Object": "objects", "Mesh": "meshes", "Material": "materials", "Image": "images",
    "NodeTree": "node_groups", "GeometryNodeTree": "node_groups", "ShaderNodeTree": "node_groups",
    "CompositorNodeTree": "node_groups", "Collection": "collections", "Texture": "textures",
    "Text": "texts", "World": "worlds", "Camera": "cameras", "Light": "lights",
    "Action": "actions", "Curve": "curves", "Armature": "armatures", "Scene": "scenes",
    "VectorFont": "fonts", "ParticleSettings": "particles", "Brush": "brushes",
}


def r(v, n=3):
    """Round a number or vector-ish value for compact output."""
    if isinstance(v, (int, bool)) or v is None:
        return v
    if isinstance(v, float):
        x = round(v, n)
        return 0.0 if x == 0 else x
    try:
        return [r(x, n) for x in v]
    except TypeError:
        return v


def deg(euler):
    return [r(math.degrees(a), 1) for a in euler]


def get_obj(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        close = _close_names(name, [o.name for o in bpy.data.objects])
        raise ValueError(f"Object not found: {name!r}" + (f". Did you mean: {close}" if close else ""))
    return obj


def _close_names(name, pool, n=5):
    import difflib
    return difflib.get_close_matches(name, pool, n=n, cutoff=0.5)


def lookup_id(fixed_type_name, value):
    """Resolve an ID datablock from a name for a pointer property/socket."""
    if value is None or not isinstance(value, str):
        return value
    coll_name = ID_COLLECTIONS.get(fixed_type_name)
    colls = [coll_name] if coll_name else list(set(ID_COLLECTIONS.values()))
    for c in colls:
        coll = getattr(bpy.data, c, None)
        if coll is not None and value in coll:
            return coll[value]
    raise ValueError(f"No {fixed_type_name or 'datablock'} named {value!r}")


def jsonable(v, depth=0):
    """Convert RNA values to JSON-friendly values."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return r(v, 4)
    if isinstance(v, bpy.types.ID):
        return v.name
    if isinstance(v, (mathutils.Vector, mathutils.Color, mathutils.Euler, mathutils.Quaternion)):
        return r(list(v), 4)
    if isinstance(v, mathutils.Matrix):
        return [r(list(row), 4) for row in v]
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    if depth < 2:
        try:
            return [jsonable(x, depth + 1) for x in v]
        except TypeError:
            pass
    return str(v)


_SKIP_PROPS = {"rna_type", "name", "bl_idname", "bl_label", "bl_description", "bl_icon",
               "is_override_data", "debug_only"}


def rna_nondefault(struct, skip=(), include_all=False):
    """Return {prop: value} for RNA properties that differ from their defaults."""
    out = {}
    for p in struct.bl_rna.properties:
        pid = p.identifier
        if pid in _SKIP_PROPS or pid in skip or p.is_hidden:
            continue
        try:
            val = getattr(struct, pid)
        except Exception:
            continue
        if p.type == "POINTER":
            if isinstance(val, bpy.types.ID):
                out[pid] = val.name
            continue
        if p.type == "COLLECTION":
            continue
        if not include_all:
            try:
                if p.type == "ENUM":
                    default = p.default_flag if p.is_enum_flag else p.default
                elif getattr(p, "array_length", 0):
                    default = tuple(p.default_array)
                    val_cmp = tuple(val)
                    if all(abs(a - b) < 1e-6 if isinstance(a, float) else a == b for a, b in zip(val_cmp, default)):
                        continue
                    out[pid] = jsonable(val)
                    continue
                else:
                    default = p.default
                if isinstance(val, float) and isinstance(default, float):
                    if abs(val - default) < 1e-6:
                        continue
                elif val == default:
                    continue
            except Exception:
                pass
        out[pid] = jsonable(val)
    return out


def world_bbox(obj, depsgraph=None):
    """World-space AABB (min, max) of an object's evaluated geometry, or None."""
    o = obj.evaluated_get(depsgraph) if depsgraph else obj
    if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL", "LATTICE", "ARMATURE"}:
        return None
    try:
        corners = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
    except Exception:
        return None
    mn = mathutils.Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    mx = mathutils.Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return mn, mx


def gn_input_get(mod, identifier):
    """Geometry Nodes modifier input value (5.2+: mod.properties.inputs.<id>.value; older: mod[id])."""
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        item = getattr(props.inputs, identifier, None)
        if item is not None:
            return item.value
    return mod[identifier]


def gn_input_set(mod, identifier, value):
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        item = getattr(props.inputs, identifier, None)
        if item is not None:
            if isinstance(value, str) and hasattr(item, "value") and not isinstance(item.value, str):
                value = lookup_id("Object" if "Object" in type(item.value).__name__ else None, value)
            item.value = value
            mod.id_data.update_tag()
            return
    mod[identifier] = value
    mod.id_data.update_tag()


def custom_shape_objects():
    """Objects used as bone display shapes (e.g. the glTF importer's Icosphere) - not real geometry."""
    shapes = set()
    for arm in bpy.data.objects:
        if arm.type == "ARMATURE" and arm.pose:
            for pb in arm.pose.bones:
                if pb.custom_shape:
                    shapes.add(pb.custom_shape.name)
    return shapes


def real_meshes(objs):
    """Filter to meshes that actually render: not hide_render, not a bone custom shape, not in an excluded
    or 'not exported' helper collection."""
    shapes = custom_shape_objects()
    out = []
    for o in objs:
        if o.type != "MESH" or o.hide_render or o.name in shapes:
            continue
        if any(c.name.lower().startswith(("gltf_not_exported", "widgets", "wgt")) or c.hide_render for c in o.users_collection):
            continue
        out.append(o)
    return out


def find_view3d():
    """Return a dict usable with context.temp_override for a 3D viewport, or {}."""
    wm = bpy.context.window_manager
    if wm is None:
        return {}
    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == "VIEW_3D":
                region = next((rg for rg in area.regions if rg.type == "WINDOW"), None)
                return {"window": window, "screen": screen, "area": area, "region": region}
    if wm.windows:
        w = wm.windows[0]
        return {"window": w, "screen": w.screen}
    return {}


def eevee_engine_id():
    items = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if cand in items:
            return cand
    return "BLENDER_WORKBENCH"


def truncate(s, limit=6000):
    if s is None:
        return s
    if len(s) <= limit:
        return s
    return s[: limit // 2] + f"\n... [{len(s) - limit} chars truncated] ...\n" + s[-limit // 2:]
