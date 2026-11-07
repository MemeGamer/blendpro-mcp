"""Model import with normalization, and Tripo Bridge status (runs inside Blender)."""
import os
import sys
import bpy
import mathutils
from .util import r, find_view3d, world_bbox


def import_model(filepath, name=None, location=(0, 0, 0), target_size=None, collection=None,
                 rest_on_ground=True, custom_props=None):
    """Import glb/gltf/fbx/obj/stl/usd(z), parent everything under one empty, optionally scale to target_size (m)."""
    filepath = os.path.abspath(filepath)
    ext = os.path.splitext(filepath)[1].lower()
    before = set(bpy.data.objects)
    ctx = find_view3d()
    with bpy.context.temp_override(**ctx):
        if ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=filepath)
        elif ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=filepath)
        elif ext == ".obj":
            bpy.ops.wm.obj_import(filepath=filepath)
        elif ext == ".stl":
            bpy.ops.wm.stl_import(filepath=filepath)
        elif ext in (".usd", ".usdz", ".usda", ".usdc"):
            bpy.ops.wm.usd_import(filepath=filepath)
        else:
            raise ValueError(f"unsupported model format {ext}")
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise RuntimeError("importer added no objects")
    coll = None
    if collection:
        coll = bpy.data.collections.get(collection) or bpy.data.collections.new(collection)
        if coll.name not in bpy.context.scene.collection.children and not any(coll.name in c.children for c in bpy.data.collections):
            bpy.context.scene.collection.children.link(coll)
    roots = [o for o in new if o.parent is None or o.parent not in new]
    root = bpy.data.objects.new(name or os.path.splitext(os.path.basename(filepath))[0], None)
    root.empty_display_type = "PLAIN_AXES"
    (coll or new[0].users_collection[0]).objects.link(root)
    for o in roots:
        mw = o.matrix_world.copy()
        o.parent = root
        o.matrix_world = mw
    if coll:
        for o in new + [root]:
            for c in list(o.users_collection):
                if c != coll:
                    c.objects.unlink(o)
            if coll not in o.users_collection:
                coll.objects.link(o)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()

    from .util import real_meshes
    geo = real_meshes(new) or new

    def bounds():
        mins, maxs = [], []
        for o in geo:
            bb = world_bbox(o, dg)
            if bb:
                mins.append(bb[0]); maxs.append(bb[1])
        if not mins:
            return None
        return (mathutils.Vector([min(v[i] for v in mins) for i in range(3)]),
                mathutils.Vector([max(v[i] for v in maxs) for i in range(3)]))
    bb = bounds()
    if bb and target_size:
        size = max(bb[1] - bb[0])
        if size > 0:
            s = float(target_size) / size
            root.scale = (s, s, s)
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            bb = bounds()
    loc = mathutils.Vector(location)
    if bb:
        center = (bb[0] + bb[1]) / 2
        offset = mathutils.Vector((loc.x - center.x, loc.y - center.y, (loc.z - bb[0].z) if rest_on_ground else (loc.z - center.z)))
        root.location += offset
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        bb = bounds()
    for k, v in (custom_props or {}).items():
        root[k] = v
    meshes = [o for o in geo if o.type == "MESH"]
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    mats = sorted({s.material.name for o in meshes for s in o.material_slots if s.material})
    imgs = sorted({f"{n.image.name}({n.image.size[0]}x{n.image.size[1]})" for m in mats
                   for n in (bpy.data.materials[m].node_tree.nodes if bpy.data.materials[m].node_tree else [])
                   if n.bl_idname == "ShaderNodeTexImage" and n.image})
    return {"root": root.name, "objects": [o.name for o in new], "meshes": len(meshes), "tris": tris,
            "materials": mats, "textures": imgs[:12],
            "bounds": [r(bb[0], 3), r(bb[1], 3)] if bb else None,
            "dimensions": r(bb[1] - bb[0], 3) if bb else None, "scale_applied_to_root": r(list(root.scale), 4)}


def tripo_bridge_status():
    """State of Tripo's own Bridge add-on (Tripo Studio -> Blender push, ws://127.0.0.1:60600)."""
    import addon_utils
    mod_name = next((m.__name__ for m in addon_utils.modules() if "tripo" in m.__name__.lower()), None)
    enabled = bool(mod_name) and mod_name in bpy.context.preferences.addons
    st = None
    for name, mod in list(sys.modules.items()):
        if name.lower().endswith("core.status_manager") and "tripo" in name.lower() and hasattr(mod, "status"):
            st = mod.status
            break
    res = {"addon": mod_name, "enabled": enabled}
    if st is not None:
        res.update({"state": getattr(st.state, "value", str(st.state)), "client": st.client_name,
                    "last_log": (st.last_log or "").format(**(st.last_log_params or {})) if st.last_log else "",
                    "issue": st.connection_issue})
    imported = [o.name for o in bpy.data.objects if any("tripo" in k.lower() for k in o.keys())]
    if imported:
        res["tripo_objects"] = imported[:30]
    return res
