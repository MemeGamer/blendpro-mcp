"""Scene understanding: overview, inspect, snapshot/diff (runs inside Blender)."""
import fnmatch
import bpy
from .util import r, deg, get_obj, rna_nondefault, jsonable, world_bbox, gn_input_get

# ---------------------------------------------------------------- snapshot / diff

_DATA_COLLS = ("objects", "meshes", "materials", "node_groups", "images", "collections",
               "actions", "cameras", "lights", "worlds", "curves", "armatures", "textures")


def _obj_state(obj):
    st = {
        "type": obj.type,
        "loc": tuple(r(obj.location, 4)),
        "rot": tuple(deg(obj.rotation_euler)),
        "scale": tuple(r(obj.scale, 4)),
        "dims": tuple(r(obj.dimensions, 4)),
        "parent": obj.parent.name if obj.parent else None,
        "data": obj.data.name if obj.data else None,
        "mods": tuple(f"{m.name}:{m.type}" for m in obj.modifiers),
        "mats": tuple(s.material.name if s.material else None for s in obj.material_slots),
        "hidden": obj.hide_get() if obj.name in bpy.context.view_layer.objects else True,
        "hide_render": obj.hide_render,
        "colls": tuple(c.name for c in obj.users_collection),
    }
    if obj.type == "MESH" and obj.data:
        me = obj.data
        if obj.mode == "EDIT":
            try:
                obj.update_from_editmode()
            except Exception:
                pass
        st["mesh"] = (len(me.vertices), len(me.edges), len(me.polygons))
    if obj.type == "LIGHT":
        st["light"] = (obj.data.type, r(obj.data.energy, 3), tuple(r(obj.data.color, 3)))
    if obj.animation_data and obj.animation_data.action:
        st["action"] = obj.animation_data.action.name
    return st


def snapshot():
    snap = {"objects": {o.name: _obj_state(o) for o in bpy.data.objects},
            "counts": {c: len(getattr(bpy.data, c)) for c in _DATA_COLLS},
            "mode": bpy.context.mode,
            "active": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
            "frame": bpy.context.scene.frame_current}
    return snap


def diff(before, after):
    """Compact description of what changed between two snapshots."""
    b, a = before["objects"], after["objects"]
    out = {}
    added = [n for n in a if n not in b]
    removed = [n for n in b if n not in a]
    if added:
        out["added"] = {n: _brief(a[n]) for n in added[:40]}
        if len(added) > 40:
            out["added_more"] = len(added) - 40
    if removed:
        out["removed"] = removed[:60]
    changed = {}
    for n in a:
        if n in b and a[n] != b[n]:
            ch = {}
            for k in set(a[n]) | set(b[n]):
                if a[n].get(k) != b[n].get(k):
                    ch[k] = [b[n].get(k), a[n].get(k)]
            changed[n] = ch
    if changed:
        items = list(changed.items())
        out["changed"] = dict(items[:40])
        if len(items) > 40:
            out["changed_more"] = len(items) - 40
    counts = {k: [before["counts"][k], after["counts"][k]] for k in after["counts"]
              if after["counts"][k] != before["counts"].get(k)}
    if counts:
        out["datablocks"] = counts
    for k in ("mode", "active", "frame"):
        if before.get(k) != after.get(k):
            out[k] = [before.get(k), after.get(k)]
    return out or {"note": "no tracked changes (object transforms/modifiers/materials/mesh counts unchanged)"}


def _brief(st):
    d = {"type": st["type"], "loc": st["loc"], "dims": st["dims"]}
    if "mesh" in st:
        d["mesh"] = st["mesh"]
    if st["mods"]:
        d["mods"] = st["mods"]
    if any(st["mats"]):
        d["mats"] = st["mats"]
    if st["parent"]:
        d["parent"] = st["parent"]
    return d


# ---------------------------------------------------------------- overview

def _fmt_vec(v, nd=2):
    return "(" + ",".join(f"{x:g}" for x in r(v, nd)) + ")"


def _obj_line(obj, depsgraph, show_eval):
    parts = [f"{obj.name} [{obj.type}]"]
    parts.append(f"loc{_fmt_vec(obj.location)}")
    if any(abs(a) > 1e-5 for a in obj.rotation_euler):
        parts.append(f"rot{_fmt_vec(deg(obj.rotation_euler), 1)}deg")
    if any(abs(s - 1) > 1e-5 for s in obj.scale):
        flag = "" if obj.type not in {"MESH", "CURVE"} else "!"  # unapplied scale on geometry
        parts.append(f"scl{_fmt_vec(obj.scale)}{flag}")
    if obj.type in {"MESH", "CURVE", "FONT", "SURFACE", "META", "CURVES"}:
        parts.append(f"dim{_fmt_vec(obj.dimensions)}")
    if obj.type == "MESH" and obj.data:
        me = obj.data
        txt = f"v{len(me.vertices)} f{len(me.polygons)}"
        if show_eval and obj.modifiers:
            try:
                ev = obj.evaluated_get(depsgraph)
                em = ev.data
                if len(em.polygons) != len(me.polygons):
                    txt += f"->eval f{len(em.polygons)}"
            except Exception:
                pass
        parts.append(txt)
    elif obj.type == "LIGHT":
        parts.append(f"{obj.data.type} {obj.data.energy:g}W")
    elif obj.type == "CAMERA":
        parts.append(f"{obj.data.type} {obj.data.lens:g}mm" if obj.data.type == "PERSP" else f"ORTHO {obj.data.ortho_scale:g}")
    elif obj.type == "EMPTY" and obj.instance_type == "COLLECTION" and obj.instance_collection:
        parts.append(f"instance:{obj.instance_collection.name}")
    if obj.modifiers:
        mods = []
        for m in obj.modifiers:
            s = m.type
            if m.type == "SUBSURF":
                s += f"({m.levels}/{m.render_levels})"
            elif m.type == "NODES" and m.node_group:
                s += f"({m.node_group.name})"
            elif m.type in {"BOOLEAN"}:
                tgt = m.object.name if m.object else (m.collection.name if m.collection else "-")
                s += f"({m.operation}:{tgt})"
            elif m.type == "MIRROR":
                s += "(" + "".join(a for a, u in zip("XYZ", m.use_axis) if u) + ")"
            elif m.type == "ARRAY":
                s += f"(x{m.count})"
            elif m.type == "BEVEL":
                s += f"({r(m.width, 3)},{m.segments}seg)"
            elif m.type == "SOLIDIFY":
                s += f"({r(m.thickness, 3)})"
            if not m.show_viewport:
                s += "[off]"
            mods.append(s)
        parts.append("mods:" + ",".join(mods))
    mats = [s.material.name if s.material else "-" for s in obj.material_slots]
    if mats:
        parts.append("mats:" + ",".join(mats))
    flags = []
    try:
        if obj.hide_get():
            flags.append("hidden")
    except Exception:
        pass
    if obj.hide_viewport:
        flags.append("viewport-disabled")
    if obj.hide_render:
        flags.append("no-render")
    if obj.select_get():
        flags.append("selected")
    if obj.animation_data and (obj.animation_data.action or obj.animation_data.drivers):
        flags.append("animated")
    if obj.constraints:
        flags.append("constraints:" + ",".join(c.type for c in obj.constraints))
    if flags:
        parts.append("{" + " ".join(flags) + "}")
    return " ".join(parts)


def scene_overview(filter=None, types=None, collection=None, limit=300, show_eval=True):
    scene = bpy.context.scene
    vl = bpy.context.view_layer
    dg = bpy.context.evaluated_depsgraph_get()
    lines = []
    rd = scene.render
    us = scene.unit_settings
    world = scene.world.name if scene.world else None
    by_type = {}
    for o in scene.objects:
        by_type[o.type] = by_type.get(o.type, 0) + 1
    lines.append(f"file: {bpy.data.filepath or '(unsaved)'}{' *modified' if bpy.data.is_dirty else ''} | blender {bpy.app.version_string}")
    lines.append(f"scene: {scene.name} | engine {rd.engine} | res {rd.resolution_x}x{rd.resolution_y}@{rd.resolution_percentage}% | "
                 f"frames {scene.frame_start}-{scene.frame_end} (cur {scene.frame_current}, {rd.fps}fps) | "
                 f"units {us.system} x{us.scale_length:g} | camera {scene.camera.name if scene.camera else None} | world {world}")
    lines.append(f"mode: {bpy.context.mode} | active: {vl.objects.active.name if vl.objects.active else None} | "
                 f"objects: {len(scene.objects)} " + str(by_type))
    lines.append(f"data: materials {len(bpy.data.materials)}, node_groups {len(bpy.data.node_groups)}, images {len(bpy.data.images)}, actions {len(bpy.data.actions)}")
    lines.append("---")

    types_set = {t.upper() for t in types} if types else None
    count = [0]

    def wanted(o):
        if types_set and o.type not in types_set:
            return False
        if filter and not fnmatch.fnmatch(o.name.lower(), filter.lower()):
            return False
        return True

    filtering = bool(filter or types_set)
    shown = set()

    def emit_obj(o, depth, coll):
        if count[0] >= limit:
            return
        if wanted(o):
            lines.append("  " * depth + _obj_line(o, dg, show_eval))
            shown.add(o.name)
            count[0] += 1
        for ch in sorted(o.children, key=lambda c: c.name):
            if coll in ch.users_collection or filtering:
                emit_obj(ch, depth + (0 if filtering else 1), coll)

    def emit_coll(coll, depth):
        if collection and coll.name != collection and depth == 0 and coll != scene.collection:
            pass
        objs = [o for o in coll.objects if (o.parent is None or o.parent.name not in coll.objects)]
        header = "  " * depth + f"[{coll.name}]" + (" (excluded)" if _layer_coll_excluded(coll) else "")
        before = len(lines)
        lines.append(header)
        for o in sorted(objs, key=lambda o: o.name):
            emit_obj(o, depth + 1, coll)
        for ch in coll.children:
            emit_coll(ch, depth + 1)
        if filtering and len(lines) == before + 1:
            lines.pop()  # drop empty collection headers when filtering

    root = scene.collection
    if collection:
        c = bpy.data.collections.get(collection)
        if c is None:
            raise ValueError(f"Collection not found: {collection}")
        root = c
    emit_coll(root, 0)
    if count[0] >= limit:
        lines.append(f"... truncated at {limit} objects (use filter/types/collection)")
    lines.append("legend: scl!=unapplied scale on geometry, ->eval=face count after modifiers, -=empty material slot")
    return "\n".join(lines)


def _layer_coll_excluded(coll):
    def find(lc):
        if lc.collection == coll:
            return lc
        for c in lc.children:
            x = find(c)
            if x:
                return x
        return None
    lc = find(bpy.context.view_layer.layer_collection)
    return bool(lc and lc.exclude)


# ---------------------------------------------------------------- inspect

def inspect(names, sections=None):
    sections = set(sections or ["transform", "data", "modifiers", "materials", "constraints", "animation", "custom"])
    dg = bpy.context.evaluated_depsgraph_get()
    if isinstance(names, str):
        names = [names]
    out = {}
    for name in names:
        obj = get_obj(name)
        d = {"type": obj.type}
        if "transform" in sections:
            bb = world_bbox(obj, dg)
            d["transform"] = {
                "location": r(obj.location, 4), "rotation_deg": deg(obj.rotation_euler),
                "rotation_mode": obj.rotation_mode, "scale": r(obj.scale, 4),
                "dimensions": r(obj.dimensions, 4),
                "world_bbox": [r(bb[0], 4), r(bb[1], 4)] if bb else None,
                "parent": obj.parent.name if obj.parent else None,
                "parent_type": obj.parent_type if obj.parent else None,
                "children": [c.name for c in obj.children],
                "collections": [c.name for c in obj.users_collection],
                "visible": obj.visible_get() if obj.name in bpy.context.view_layer.objects else False,
                "hide_render": obj.hide_render,
            }
            if obj.matrix_world.is_negative:
                d["transform"]["warning"] = "negative scale (flipped normals on export)"
        if "data" in sections and obj.data is not None:
            d["data"] = _data_info(obj, dg)
        if "modifiers" in sections and obj.modifiers:
            d["modifiers"] = [_modifier_info(m) for m in obj.modifiers]
        if "materials" in sections and obj.material_slots:
            d["materials"] = [_material_brief(s) for s in obj.material_slots]
        if "constraints" in sections and obj.constraints:
            d["constraints"] = [{"name": c.name, "type": c.type, **rna_nondefault(c, skip={"type", "is_valid", "error_location", "error_rotation"})} for c in obj.constraints]
        if "animation" in sections and obj.animation_data:
            ad = obj.animation_data
            a = {}
            if ad.action:
                act = ad.action
                a["action"] = act.name
                a["frame_range"] = r(list(act.frame_range), 1)
                try:
                    fcs = list(act.fcurves)
                except AttributeError:  # layered actions (4.4+)
                    fcs = []
                    for layer in getattr(act, "layers", []):
                        for strip in layer.strips:
                            for bag in getattr(strip, "channelbags", []):
                                fcs.extend(bag.fcurves)
                a["fcurves"] = [f"{f.data_path}[{f.array_index}] ({len(f.keyframe_points)} keys)" for f in fcs[:30]]
            if ad.drivers:
                a["drivers"] = [f"{f.data_path}[{f.array_index}]: {f.driver.expression}" for f in ad.drivers[:20]]
            if ad.nla_tracks:
                a["nla_tracks"] = [t.name for t in ad.nla_tracks]
            d["animation"] = a
        if "custom" in sections:
            keys = [k for k in obj.keys() if not k.startswith("_")]
            if keys:
                d["custom_props"] = {k: jsonable(obj[k]) for k in keys}
        out[name] = d
    return out


def _data_info(obj, dg):
    data = obj.data
    t = obj.type
    if t == "MESH":
        me = data
        info = {"name": me.name, "users": me.users, "verts": len(me.vertices), "edges": len(me.edges),
                "faces": len(me.polygons), "tris": sum(len(p.vertices) - 2 for p in me.polygons),
                "uv_layers": [u.name for u in me.uv_layers],
                "color_attributes": [c.name for c in me.color_attributes],
                "attributes": [f"{a.name}:{a.domain}:{a.data_type}" for a in me.attributes if not a.name.startswith((".", "_")) and a.name not in {"position", "sharp_face", "material_index"}],
                "vertex_groups": [g.name for g in obj.vertex_groups][:50],
                "smooth_faces": sum(1 for p in me.polygons if p.use_smooth)}
        if me.shape_keys:
            info["shape_keys"] = [k.name for k in me.shape_keys.key_blocks]
        if obj.modifiers:
            ev = obj.evaluated_get(dg)
            em = ev.data
            info["evaluated"] = {"verts": len(em.vertices), "faces": len(em.polygons),
                                 "tris": sum(len(p.vertices) - 2 for p in em.polygons)}
        return info
    if t == "LIGHT":
        return {"name": data.name, **rna_nondefault(data, skip={"use_nodes", "node_tree"}, include_all=False), "type": data.type, "energy": r(data.energy, 3), "color": r(data.color, 3)}
    if t == "CAMERA":
        sc = bpy.context.scene
        return {"name": data.name, "type": data.type, "lens": data.lens, "sensor_width": data.sensor_width,
                "clip": [data.clip_start, data.clip_end], "ortho_scale": data.ortho_scale,
                "dof": data.dof.use_dof, "is_scene_camera": sc.camera == obj}
    if t in {"CURVE", "FONT", "SURFACE"}:
        info = {"name": data.name, "dimensions": data.dimensions, "bevel_depth": r(data.bevel_depth, 4),
                "extrude": r(data.extrude, 4), "resolution_u": data.resolution_u}
        if t != "FONT":
            info["splines"] = [f"{s.type}:{len(s.bezier_points) or len(s.points)}pts{' cyclic' if s.use_cyclic_u else ''}" for s in data.splines][:20]
        else:
            info["body"] = data.body[:200]
        return info
    if t == "ARMATURE":
        return {"name": data.name, "bones": len(data.bones), "root_bones": [b.name for b in data.bones if not b.parent][:20],
                "pose_position": data.pose_position}
    if t == "EMPTY":
        return {"display": obj.empty_display_type, "size": obj.empty_display_size}
    return {"name": getattr(data, "name", None)}


def _modifier_info(m):
    d = {"name": m.name, "type": m.type}
    if not m.show_viewport:
        d["show_viewport"] = False
    if not m.show_render:
        d["show_render"] = False
    if m.type == "NODES":
        d["node_group"] = m.node_group.name if m.node_group else None
        if m.node_group:
            inputs = {}
            for item in m.node_group.interface.items_tree:
                if getattr(item, "item_type", "") == "SOCKET" and item.in_out == "INPUT" and item.socket_type != "NodeSocketGeometry":
                    try:
                        inputs[item.name] = jsonable(gn_input_get(m, item.identifier))
                    except Exception:
                        pass
            d["inputs"] = inputs
        return d
    d.update(rna_nondefault(m, skip={"type", "show_viewport", "show_render", "show_in_editmode", "show_on_cage",
                                      "show_expanded", "is_active", "is_override_data", "persistent_uid",
                                      "use_pin_to_last", "execution_time", "use_apply_on_spline"}))
    return d


def _material_brief(slot):
    m = slot.material
    if m is None:
        return {"slot": slot.name, "material": None}
    d = {"material": m.name, "link": slot.link}
    nt = m.node_tree
    if nt:
        d["nodes"] = len(nt.nodes)
        out = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output), None)
        if out and out.inputs["Surface"].is_linked:
            sh = out.inputs["Surface"].links[0].from_node
            d["surface"] = sh.bl_idname
            if sh.bl_idname == "ShaderNodeBsdfPrincipled":
                vals = {}
                for key in ("Base Color", "Metallic", "Roughness", "Alpha", "Transmission Weight", "Emission Strength"):
                    s = sh.inputs.get(key)
                    if s is None:
                        continue
                    vals[key] = ("linked:" + s.links[0].from_node.name) if s.is_linked else jsonable(s.default_value)
                d["principled"] = vals
        imgs = [n.image.name + f"({n.image.size[0]}x{n.image.size[1]})" for n in nt.nodes if n.bl_idname == "ShaderNodeTexImage" and n.image]
        if imgs:
            d["images"] = imgs
    else:
        d["diffuse_color"] = r(m.diffuse_color, 3)
    return d
