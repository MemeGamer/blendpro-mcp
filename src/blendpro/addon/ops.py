"""Execution, operators, checkpoints, API lookup, add-ons, export (runs inside Blender)."""
import difflib
import io
import json
import math
import os
import tempfile
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr

import bpy
import bmesh
import mathutils

from . import scene as scn
from .util import r, get_obj, jsonable, find_view3d, truncate

_STATE = {"original_filepath": None, "persist": {}}


def _undo_push(msg):
    if bpy.app.background:
        return
    ctx = find_view3d()
    try:
        with bpy.context.temp_override(**ctx):
            bpy.ops.ed.undo_push(message=msg)
    except Exception:
        pass


# ---------------------------------------------------------------- python

def run_python(code, diff=True, max_output=6000):
    """Exec code with helpers; return stdout, `result` variable, errors and a scene diff."""
    before = scn.snapshot() if diff else None
    ns = {
        "bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "math": math,
        "Vector": mathutils.Vector, "Matrix": mathutils.Matrix, "Euler": mathutils.Euler,
        "C": bpy.context, "D": bpy.data, "obj": get_obj, "persist": _STATE["persist"],
        "radians": math.radians, "degrees": math.degrees,
    }
    buf = io.StringIO()
    t0 = time.time()
    ok = True
    err = tb = None
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            exec(compile(code, "<blendpro>", "exec"), ns)
    except Exception as e:
        ok = False
        err = f"{type(e).__name__}: {e}"
        tb_lines = traceback.format_exc().splitlines()
        # keep only frames from the user code
        tb = "\n".join(l for l in tb_lines if "<blendpro>" in l or not l.startswith("  File")) [-3000:]
        # include the failing line
        try:
            import re
            m = re.findall(r'File "<blendpro>", line (\d+)', traceback.format_exc())
            if m:
                ln = int(m[-1])
                src = code.splitlines()
                tb += f"\n>>> line {ln}: {src[ln - 1].strip() if 0 < ln <= len(src) else ''}"
        except Exception:
            pass
        if isinstance(e, (KeyError, AttributeError)):
            hint = _attr_hint(e)
            if hint:
                tb += f"\nhint: {hint}"
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    out = {"ok": ok, "seconds": round(time.time() - t0, 3)}
    text = buf.getvalue()
    if text:
        out["stdout"] = truncate(text, max_output)
    if "result" in ns:
        try:
            s = json.dumps(ns["result"], default=lambda o: jsonable(o))
            out["result"] = json.loads(s) if len(s) <= max_output else truncate(s, max_output)
        except Exception:
            out["result"] = truncate(repr(ns["result"]), max_output)
    if not ok:
        out["error"] = err
        out["traceback"] = tb
    if diff:
        out["diff"] = scn.diff(before, scn.snapshot())
    if ok:
        _undo_push("BlendPro: python")
    return out


def _attr_hint(e):
    msg = str(e)
    import re
    m = re.search(r"'(\w+)' object has no attribute '(\w+)'", msg)
    if m:
        tname, attr = m.groups()
        t = getattr(bpy.types, tname, None)
        if t is not None:
            keys = [p.identifier for p in t.bl_rna.properties] + [f for f in dir(t) if not f.startswith("_")]
            close = difflib.get_close_matches(attr, keys, n=5, cutoff=0.5)
            return f"{tname} has no '{attr}'. Close: {close}" if close else f"use api_lookup('struct:{tname}')"
    m = re.search(r'key "([^"]+)" not found', msg)
    if m:
        return f"key {m.group(1)!r} not found - for node sockets use api_lookup('node:<type>') to list socket names"
    return None


# ---------------------------------------------------------------- operators with context

def run_operators(steps, diff=True, stop_on_error=True):
    """Run bpy.ops calls with automatic context/mode/selection handling."""
    before = scn.snapshot() if diff else None
    results = []
    base_ctx = find_view3d()
    for i, st in enumerate(steps):
        opname = st.get("op", "")
        rec = {"i": i, "op": opname}
        try:
            mod, _, fn = opname.partition(".")
            op = getattr(getattr(bpy.ops, mod), fn)
            names = st.get("objects") or ([st["object"]] if st.get("object") else None)
            vl = bpy.context.view_layer
            if names:
                objs = [get_obj(n) for n in names]
                if bpy.context.mode != "OBJECT" and st.get("mode", "OBJECT") != bpy.context.mode:
                    _mode_set("OBJECT", base_ctx)
                if bpy.context.mode == "OBJECT":
                    for o in vl.objects:
                        o.select_set(False)
                for o in objs:
                    o.hide_set(False)
                    o.select_set(True)
                vl.objects.active = objs[0]
            mode = st.get("mode")
            if mode:
                _mode_set(mode, base_ctx)
            if st.get("select_all") is not None and bpy.context.mode.startswith("EDIT"):
                with bpy.context.temp_override(**base_ctx):
                    if bpy.context.mode == "EDIT_MESH":
                        bpy.ops.mesh.select_all(action="SELECT" if st["select_all"] else "DESELECT")
            if st.get("select_mode") and bpy.context.mode == "EDIT_MESH":
                with bpy.context.temp_override(**base_ctx):
                    bpy.ops.mesh.select_mode(type=st["select_mode"].upper())
            ctx = dict(base_ctx)
            ao = vl.objects.active
            if ao is not None:
                ctx.update({"active_object": ao, "object": ao})
                if bpy.context.mode == "OBJECT":
                    sel = [o for o in vl.objects if o.select_get()]
                    ctx.update({"selected_objects": sel, "selected_editable_objects": sel})
            args = st.get("args") or {}
            with bpy.context.temp_override(**ctx):
                if not op.poll():
                    raise RuntimeError(f"poll() failed for {opname} in mode {bpy.context.mode} "
                                       f"(active={ao.name if ao else None}). Check mode/selection/context.")
                res = op(**args)
            rec["result"] = sorted(res)
            if st.get("then_mode"):
                _mode_set(st["then_mode"], base_ctx)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            if "keyword" in str(e) or "unrecognized" in str(e).lower():
                rec["hint"] = f"api_lookup('op:{opname}') lists valid args"
            results.append(rec)
            if stop_on_error:
                break
            continue
        results.append(rec)
    out = {"steps": results, "mode": bpy.context.mode}
    if diff:
        out["diff"] = scn.diff(before, scn.snapshot())
    _undo_push("BlendPro: operators")
    return out


def _mode_set(mode, base_ctx):
    mode = mode.upper()
    target = {"EDIT_MESH": "EDIT", "EDIT": "EDIT"}.get(mode, mode)
    if bpy.context.mode == mode or (target == "EDIT" and bpy.context.mode.startswith("EDIT")):
        return
    vl = bpy.context.view_layer
    ctx = dict(base_ctx)
    if vl.objects.active:
        ctx.update({"active_object": vl.objects.active, "object": vl.objects.active})
    with bpy.context.temp_override(**ctx):
        bpy.ops.object.mode_set(mode=target)


# ---------------------------------------------------------------- simple object edits (legacy-compatible)

PRIMS = {"CUBE": "primitive_cube_add", "SPHERE": "primitive_uv_sphere_add", "UV_SPHERE": "primitive_uv_sphere_add",
         "ICO_SPHERE": "primitive_ico_sphere_add", "CYLINDER": "primitive_cylinder_add", "CONE": "primitive_cone_add",
         "PLANE": "primitive_plane_add", "TORUS": "primitive_torus_add", "CIRCLE": "primitive_circle_add",
         "MONKEY": "primitive_monkey_add", "GRID": "primitive_grid_add"}


def edit_objects(operations):
    """Batch of simple ops: add, transform, delete, duplicate, material, modifier, parent, rename, apply."""
    before = scn.snapshot()
    results = []
    for i, op in enumerate(operations):
        kind = op.get("op")
        try:
            results.append({"i": i, "op": kind, "ok": True, **(_edit_one(kind, op) or {})})
        except Exception as e:
            results.append({"i": i, "op": kind, "ok": False, "error": f"{type(e).__name__}: {e}"})
    bpy.context.view_layer.update()
    _undo_push("BlendPro: edit")
    return {"results": results, "diff": scn.diff(before, scn.snapshot())}


def _edit_one(kind, op):
    if kind == "add":
        ptype = op.get("primitive", "CUBE").upper()
        if ptype in ("EMPTY",):
            o = bpy.data.objects.new(op.get("name", "Empty"), None)
            bpy.context.collection.objects.link(o)
        else:
            fn = getattr(bpy.ops.mesh, PRIMS[ptype])
            kw = dict(op.get("args") or {})
            with bpy.context.temp_override(**find_view3d()):
                fn(**kw)
            o = bpy.context.view_layer.objects.active
        if op.get("name"):
            o.name = op["name"]
        _transform(o, op)
        if op.get("collection"):
            c = bpy.data.collections.get(op["collection"]) or bpy.data.collections.new(op["collection"])
            if c.name not in bpy.context.scene.collection.children and not any(c.name in p.children for p in bpy.data.collections):
                bpy.context.scene.collection.children.link(c)
            for uc in list(o.users_collection):
                uc.objects.unlink(o)
            c.objects.link(o)
        return {"name": o.name}
    o = get_obj(op["name"])
    if kind == "transform":
        _transform(o, op)
    elif kind == "delete":
        bpy.data.objects.remove(o, do_unlink=True)
    elif kind == "duplicate":
        n = o.copy()
        if o.data and not op.get("linked"):
            n.data = o.data.copy()
        for c in o.users_collection:
            c.objects.link(n)
        if op.get("new_name"):
            n.name = op["new_name"]
        off = op.get("offset")
        if off:
            n.location = o.location + mathutils.Vector(off)
        _transform(n, op)
        return {"name": n.name}
    elif kind == "rename":
        o.name = op["new_name"]
        return {"name": o.name}
    elif kind == "material":
        mat = bpy.data.materials.get(op.get("material") or f"{o.name}_Mat") or bpy.data.materials.new(op.get("material") or f"{o.name}_Mat")
        if hasattr(mat, "use_nodes") and not mat.use_nodes:
            mat.use_nodes = True
        bsdf = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        from .nodes import set_socket_value
        for k, v in (op.get("inputs") or {}).items():
            set_socket_value(bsdf.inputs[k], v)
        if "color" in op:
            set_socket_value(bsdf.inputs["Base Color"], op["color"])
            mat.diffuse_color = (list(bsdf.inputs["Base Color"].default_value))
        for k in ("metallic", "roughness"):
            if k in op:
                bsdf.inputs[k.capitalize()].default_value = op[k]
        slot = op.get("slot")
        if slot is None:
            if o.data.materials:
                o.data.materials[0] = mat
            else:
                o.data.materials.append(mat)
        elif slot == "append":
            o.data.materials.append(mat)
        else:
            o.data.materials[int(slot)] = mat
        return {"material": mat.name}
    elif kind == "modifier":
        m = o.modifiers.get(op.get("mod_name", "")) if op.get("mod_name") else None
        if m is None:
            m = o.modifiers.new(op.get("mod_name") or op["type"].title(), op["type"].upper())
        for k, v in (op.get("props") or {}).items():
            p = m.bl_rna.properties.get(k)
            if p is None:
                close = difflib.get_close_matches(k, [q.identifier for q in m.bl_rna.properties], n=5)
                raise KeyError(f"{m.type} modifier has no '{k}'. Close: {close}")
            if p.type == "POINTER" and isinstance(v, str):
                from .util import lookup_id
                v = lookup_id(p.fixed_type.identifier, v)
            setattr(m, k, v)
        if "index" in op:
            with bpy.context.temp_override(object=o, active_object=o):
                bpy.ops.object.modifier_move_to_index(modifier=m.name, index=op["index"])
        return {"modifier": m.name}
    elif kind == "remove_modifier":
        o.modifiers.remove(o.modifiers[op["mod_name"]])
    elif kind == "apply_modifier":
        with bpy.context.temp_override(**find_view3d(), object=o, active_object=o, selected_objects=[o]):
            bpy.ops.object.modifier_apply(modifier=op["mod_name"])
    elif kind == "apply_transform":
        for x in bpy.context.view_layer.objects:
            x.select_set(False)
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        with bpy.context.temp_override(**find_view3d(), object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o]):
            bpy.ops.object.transform_apply(location=op.get("location", False), rotation=op.get("rotation", True),
                                           scale=op.get("scale", True))
    elif kind == "parent":
        parent = get_obj(op["parent"]) if op.get("parent") else None
        mw = o.matrix_world.copy()
        o.parent = parent
        if parent is not None and op.get("keep_transform", True):
            o.matrix_parent_inverse = parent.matrix_world.inverted()
        o.matrix_world = mw
    elif kind == "visibility":
        if "hide" in op:
            o.hide_set(bool(op["hide"]))
        if "hide_render" in op:
            o.hide_render = bool(op["hide_render"])
    elif kind == "shade":
        smooth = op.get("smooth", True)
        for p in o.data.polygons:
            p.use_smooth = smooth
        if op.get("auto_smooth_angle") is not None:
            with bpy.context.temp_override(**find_view3d(), object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o]):
                bpy.ops.object.shade_auto_smooth(angle=math.radians(op["auto_smooth_angle"]))
    elif kind == "origin":
        with bpy.context.temp_override(**find_view3d(), object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o]):
            bpy.ops.object.origin_set(type=op.get("type", "ORIGIN_GEOMETRY"), center=op.get("center", "BOUNDS"))
    else:
        raise ValueError("op must be one of add, transform, delete, duplicate, rename, material, modifier, "
                         "remove_modifier, apply_modifier, apply_transform, parent, visibility, shade, origin")
    return None


def _transform(o, op):
    if op.get("location") is not None:
        o.location = op["location"]
    if op.get("rotation") is not None:  # degrees
        o.rotation_euler = [math.radians(a) for a in op["rotation"]]
    if op.get("scale") is not None:
        s = op["scale"]
        o.scale = [s, s, s] if isinstance(s, (int, float)) else s
    if op.get("dimensions") is not None:
        bpy.context.view_layer.update()
        o.dimensions = op["dimensions"]


# ---------------------------------------------------------------- checkpoints

def _ckpt_dir():
    d = os.path.join(tempfile.gettempdir(), "blendpro_checkpoints")
    os.makedirs(d, exist_ok=True)
    return d


def checkpoint(action="save", name=None):
    d = _ckpt_dir()
    if action == "list":
        items = sorted(((f[:-6], os.path.getmtime(os.path.join(d, f))) for f in os.listdir(d) if f.endswith(".blend")), key=lambda x: -x[1])
        return {"checkpoints": [{"name": n, "age_s": round(time.time() - t)} for n, t in items[:30]],
                "original_file": _STATE["original_filepath"] or bpy.data.filepath}
    if not name:
        name = time.strftime("ckpt_%H%M%S")
    path = os.path.join(d, f"{name}.blend")
    if action == "save":
        if _STATE["original_filepath"] is None:
            _STATE["original_filepath"] = bpy.data.filepath
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=False)
        return {"saved": name, "path": path, "size_kb": os.path.getsize(path) // 1024}
    if action == "restore":
        if not os.path.exists(path):
            raise ValueError(f"No checkpoint {name}")
        orig = _STATE["original_filepath"] if _STATE["original_filepath"] is not None else bpy.data.filepath
        _STATE["original_filepath"] = orig
        ctx = find_view3d()
        with bpy.context.temp_override(**{k: v for k, v in ctx.items() if k == "window"}):
            bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        return {"restored": name, "note": f"Current file path is now the checkpoint copy. Original file: {orig or '(unsaved)'} - "
                                          f"use save_file(path) to write back when ready."}
    raise ValueError("action must be save, restore or list")


def save_file(path=None, copy=False):
    target = path or _STATE["original_filepath"] or bpy.data.filepath
    if not target:
        raise ValueError("No path given and the file has never been saved")
    target = os.path.abspath(target)
    bpy.ops.wm.save_as_mainfile(filepath=target, copy=copy)
    if not copy:
        _STATE["original_filepath"] = None
    return {"saved": target}


def open_file(path):
    with bpy.context.temp_override(**{k: v for k, v in find_view3d().items() if k == "window"}):
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(path))
    _STATE["original_filepath"] = None
    return {"opened": bpy.data.filepath, "objects": len(bpy.context.scene.objects)}


# ---------------------------------------------------------------- API lookup

def api_lookup(query, limit=40):
    """op:<mod.name> | node:<type or search> | struct:<TypeName> | path:<python expr> | search:<text> | enum:<Type.prop>"""
    kind, _, q = query.partition(":")
    kind = kind.strip().lower()
    q = q.strip()
    if kind == "op":
        mod, _, fn = q.partition(".")
        op = getattr(getattr(bpy.ops, mod), fn)
        rna = op.get_rna_type()
        props = []
        for p in rna.properties:
            if p.identifier == "rna_type":
                continue
            props.append(_prop_desc(p))
        return {"op": q, "description": rna.description, "args": props}
    if kind == "node":
        return _node_lookup(q, limit)
    if kind == "struct":
        t = getattr(bpy.types, q, None)
        if t is None:
            close = difflib.get_close_matches(q, dir(bpy.types), n=8)
            raise ValueError(f"No bpy.types.{q}. Close: {close}")
        props = [_prop_desc(p) for p in t.bl_rna.properties if p.identifier != "rna_type"]
        funcs = [f.identifier + "(" + ", ".join(a.identifier for a in f.parameters if not a.is_output) + ")" for f in t.bl_rna.functions]
        return {"struct": q, "base": t.bl_rna.base.identifier if t.bl_rna.base else None, "properties": props[:200], "functions": funcs[:80]}
    if kind == "path":
        val = eval(q, {"bpy": bpy, "C": bpy.context, "D": bpy.data, "mathutils": mathutils})
        if hasattr(val, "bl_rna"):
            out = {}
            for p in val.bl_rna.properties:
                if p.identifier == "rna_type":
                    continue
                try:
                    v = getattr(val, p.identifier)
                except Exception:
                    continue
                if p.type == "COLLECTION":
                    out[p.identifier] = f"<collection len={len(v)}>"
                elif p.type == "POINTER" and not isinstance(v, bpy.types.ID):
                    out[p.identifier] = f"<{p.fixed_type.identifier}>"
                else:
                    out[p.identifier] = jsonable(v)
            return {"path": q, "type": val.bl_rna.identifier, "values": out}
        return {"path": q, "value": jsonable(val)}
    if kind == "enum":
        tname, _, pname = q.partition(".")
        p = getattr(bpy.types, tname).bl_rna.properties[pname]
        return {"enum": q, "items": [f"{e.identifier}: {e.name}" for e in p.enum_items]}
    if kind == "search":
        return _search(q, limit)
    raise ValueError("query must start with op:, node:, struct:, path:, enum: or search:")


def _prop_desc(p):
    d = f"{p.identifier}:{p.type}"
    try:
        if p.type == "ENUM":
            items = [e.identifier for e in p.enum_items]
            d += ("{flag}" if p.is_enum_flag else "") + f"[{'|'.join(items[:25])}{'|...' if len(items) > 25 else ''}]"
            dv = p.default_flag if p.is_enum_flag else p.default
            d += f"={sorted(dv) if isinstance(dv, set) else dv}"
        elif p.type == "POINTER" or p.type == "COLLECTION":
            d += f"<{p.fixed_type.identifier}>"
        elif getattr(p, "array_length", 0):
            d += f"[{p.array_length}]={r(list(p.default_array), 3)}"
        else:
            d += f"={r(p.default, 4) if isinstance(p.default, float) else p.default}"
            if p.type in {"FLOAT", "INT"} and (p.hard_min > -1e9 or p.hard_max < 1e9):
                d += f" ({r(p.hard_min, 3)}..{r(p.hard_max, 3)})"
    except Exception:
        pass
    if p.is_readonly:
        d += " RO"
    return d


def _node_classes():
    out = []
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        try:
            if issubclass(cls, bpy.types.Node) and name not in ("Node", "ShaderNode", "GeometryNode", "CompositorNode", "TextureNode", "FunctionNode", "NodeInternal", "CustomNode"):
                out.append(cls)
        except TypeError:
            pass
    return out


def _node_lookup(q, limit):
    classes = _node_classes()
    exact = [c for c in classes if c.__name__ == q]
    if not exact:
        ql = q.lower()
        scored = []
        for c in classes:
            label = getattr(c.bl_rna, "name", "") or ""
            s = 0
            if ql == label.lower():
                s = 3
            elif ql in label.lower():
                s = 2
            elif ql in c.__name__.lower():
                s = 1
            if s:
                scored.append((-s, len(c.__name__), c))
        scored.sort(key=lambda x: (x[0], x[1]))
        if len(scored) != 1 and not (scored and scored[0][0] == -3):
            return {"matches": [f"{c.__name__} - {c.bl_rna.name}" for _, _, c in scored[:limit]],
                    "hint": "call again with node:<exact bl_idname> for sockets/props"}
        exact = [scored[0][2]] if scored else []
    if not exact:
        raise ValueError(f"No node matching {q!r}")
    cls = exact[0]
    name = cls.__name__
    tree_type = "ShaderNodeTree"
    if name.startswith(("GeometryNode", "FunctionNode")):
        tree_type = "GeometryNodeTree"
    elif name.startswith("CompositorNode"):
        tree_type = "CompositorNodeTree"
    elif name.startswith("TextureNode"):
        tree_type = "TextureNodeTree"
    tree = bpy.data.node_groups.new(".bp_lookup", tree_type)
    try:
        try:
            n = tree.nodes.new(name)
        except RuntimeError:
            # some ShaderNode math/mix types are shared with geometry trees
            bpy.data.node_groups.remove(tree)
            tree = bpy.data.node_groups.new(".bp_lookup", "GeometryNodeTree")
            n = tree.nodes.new(name)
        from .nodes import _BASE_NODE_PROPS
        props = [_prop_desc(p) for p in n.bl_rna.properties if p.identifier not in _BASE_NODE_PROPS]

        def socks(ss):
            res = []
            for i, s in enumerate(ss):
                v = None
                if hasattr(s, "default_value"):
                    try:
                        v = jsonable(s.default_value)
                    except Exception:
                        pass
                res.append(f"{i}:{s.name}({s.type})" + (f"={v}" if v is not None else "") + ("" if s.enabled else " [disabled]"))
            return res
        return {"node": name, "label": cls.bl_rna.name, "tree": tree_type, "props": props,
                "inputs": socks(n.inputs), "outputs": socks(n.outputs),
                "note": "disabled sockets depend on props (e.g. data_type/operation); sockets with duplicate names -> use index"}
    finally:
        bpy.data.node_groups.remove(tree)


def _search(q, limit):
    ql = q.lower()
    hits = []
    for mod in dir(bpy.ops):
        if mod.startswith("_"):
            continue
        try:
            submod = getattr(bpy.ops, mod)
            for fn in dir(submod):
                if fn.startswith("_"):
                    continue
                full = f"{mod}.{fn}"
                if ql in full.lower():
                    hits.append(f"op:{full}")
                else:
                    try:
                        rna = getattr(submod, fn).get_rna_type()
                        if ql in rna.name.lower() or ql in (rna.description or "").lower():
                            hits.append(f"op:{full} - {rna.name}")
                    except Exception:
                        pass
        except Exception:
            pass
        if len(hits) > limit * 3:
            break
    for c in _node_classes():
        if ql in c.__name__.lower() or ql in (c.bl_rna.name or "").lower():
            hits.append(f"node:{c.__name__} - {c.bl_rna.name}")
    for name in dir(bpy.types):
        if ql in name.lower() and not name.endswith(("Node",)):
            hits.append(f"struct:{name}")
    return {"query": q, "hits": hits[:limit], "total": len(hits)}


# ---------------------------------------------------------------- add-ons

def addons(action="list", module=None, filepath=None):
    import addon_utils
    if action == "list":
        enabled = sorted(a.module for a in bpy.context.preferences.addons)
        available = []
        for m in addon_utils.modules():
            info = addon_utils.module_bl_info(m)
            available.append(f"{m.__name__} - {info.get('name')}")
        return {"enabled": enabled, "available": sorted(available)[:300]}
    if action == "enable":
        mod = addon_utils.enable(module, default_set=True, persistent=True)
        if mod is None:
            raise RuntimeError(f"Could not enable {module} (see Blender console)")
        _save_prefs()
        return {"enabled": module, "operators": _addon_ops(module)}
    if action == "disable":
        addon_utils.disable(module, default_set=True)
        _save_prefs()
        return {"disabled": module}
    if action == "operators":
        return {"module": module, "operators": _addon_ops(module)}
    if action == "install":
        if not filepath or not os.path.exists(filepath):
            raise ValueError("install needs an existing .zip/.py filepath")
        before = {m.__name__ for m in addon_utils.modules(refresh=True)}
        if filepath.endswith(".zip") and _is_extension_zip(filepath):
            bpy.ops.extensions.package_install_files(filepath=filepath, repo="user_default", enable_on_install=True)
        else:
            bpy.ops.preferences.addon_install(filepath=filepath, overwrite=True)
        after = {m.__name__ for m in addon_utils.modules(refresh=True)}
        new = sorted(after - before)
        for m in new:
            if not m.startswith("bl_ext."):
                addon_utils.enable(m, default_set=True, persistent=True)
        _save_prefs()
        return {"installed": new, "operators": {m: _addon_ops(m) for m in new}}
    raise ValueError("action must be list, enable, disable, operators or install")


def _is_extension_zip(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        return any(n.endswith("blender_manifest.toml") for n in z.namelist())


def _save_prefs():
    if bpy.app.background:
        return
    try:
        bpy.ops.wm.save_userpref()
    except Exception:
        pass


def _addon_ops(module):
    out = []
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        try:
            if issubclass(cls, bpy.types.Operator) and getattr(cls, "__module__", "").startswith(module):
                out.append(cls.bl_idname)
        except TypeError:
            pass
    return sorted(out)[:150]


# ---------------------------------------------------------------- export

EXPORT_PRESETS = {
    "unity_fbx": {"fmt": "fbx", "kw": dict(apply_scale_options="FBX_SCALE_ALL", axis_forward="-Z", axis_up="Y",
                                            apply_unit_scale=True, bake_space_transform=True, use_mesh_modifiers=True,
                                            mesh_smooth_type="FACE", add_leaf_bones=False, use_tspace=True,
                                            object_types={"MESH", "ARMATURE", "EMPTY"}, path_mode="COPY", embed_textures=False)},
    "glb": {"fmt": "gltf", "kw": dict(export_format="GLB", export_apply=True, export_yup=True)},
    "webxr_glb": {"fmt": "gltf", "kw": dict(export_format="GLB", export_apply=True, export_yup=True,
                                             export_draco_mesh_compression_enable=True, export_image_format="AUTO")},
    "obj": {"fmt": "obj", "kw": dict(apply_modifiers=True, export_materials=True)},
    "usd": {"fmt": "usd", "kw": dict()},
}


def export_asset(filepath, objects=None, preset="glb", options=None, check=True):
    if preset not in EXPORT_PRESETS:
        raise ValueError(f"preset must be one of {list(EXPORT_PRESETS)}")
    p = EXPORT_PRESETS[preset]
    kw = dict(p["kw"])
    kw.update(options or {})
    filepath = os.path.abspath(filepath)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    vl = bpy.context.view_layer
    prev_sel = [o for o in vl.objects if o.select_get()]
    prev_active = vl.objects.active
    objs = [get_obj(n) for n in objects] if objects else None
    report = {}
    if check and objs:
        from .analysis import mesh_analysis
        target = "quest" if "webxr" in preset else ("unity" if "unity" in preset else None)
        meshes = [o.name for o in objs if o.type == "MESH"]
        ana = mesh_analysis(meshes, target=target, self_intersections=False) if meshes else {}
        report["tris"] = sum(a["counts"]["tris"] for a in ana.values() if "counts" in a)
        issues = {n: a["issues"] for n, a in ana.items() if a.get("issues")}
        if issues:
            report["issues"] = issues
    try:
        if objs:
            for o in vl.objects:
                o.select_set(False)
            for o in objs:
                o.select_set(True)
            vl.objects.active = objs[0]
        sel_kw = "use_selection"
        if p["fmt"] in ("obj", "usd"):
            sel_kw = "export_selected_objects" if p["fmt"] == "obj" else "selected_objects_only"
        kw[sel_kw] = bool(objs)
        ctx = find_view3d()
        with bpy.context.temp_override(**ctx):
            if p["fmt"] == "fbx":
                bpy.ops.export_scene.fbx(filepath=filepath, **kw)
            elif p["fmt"] == "gltf":
                bpy.ops.export_scene.gltf(filepath=filepath, **kw)
            elif p["fmt"] == "obj":
                bpy.ops.wm.obj_export(filepath=filepath, **kw)
            elif p["fmt"] == "usd":
                bpy.ops.wm.usd_export(filepath=filepath, **kw)
    finally:
        for o in vl.objects:
            o.select_set(o in prev_sel)
        vl.objects.active = prev_active
    report.update({"filepath": filepath, "size_kb": round(os.path.getsize(filepath) / 1024, 1), "preset": preset,
                   "options": {k: (sorted(v) if isinstance(v, set) else v) for k, v in kw.items()}})
    return report
