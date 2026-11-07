"""Node tree read/write as compact JSON (materials, geometry nodes, world, compositor)."""
import difflib
import bpy
from .util import r, jsonable, lookup_id, get_obj

_BASE_NODE_PROPS = set(bpy.types.Node.bl_rna.properties.keys()) | {
    "location", "location_absolute", "width", "height", "dimensions", "internal_links", "inputs",
    "outputs", "type", "warning_propagation", "parent", "color", "use_custom_color", "select",
    "show_options", "show_preview", "hide", "mute", "show_texture", "label", "name", "color_tag",
    "bl_static_type",
}


# ---------------------------------------------------------------- target resolution

def resolve_tree(target, create=True):
    """Return (node_tree, description, owner_obj_or_None)."""
    t = target.strip()
    if t.startswith("material:"):
        name = t.split(":", 1)[1]
        mat = bpy.data.materials.get(name)
        if mat is None:
            if not create:
                raise ValueError(f"Material not found: {name}")
            mat = bpy.data.materials.new(name)
        if hasattr(mat, "use_nodes") and not mat.use_nodes:
            mat.use_nodes = True
        return mat.node_tree, f"material {mat.name}", None
    if t.startswith("object:"):
        rest = t.split(":", 1)[1]
        oname, _, kind = rest.partition("/")
        obj = get_obj(oname)
        kind = kind or "material"
        if kind.startswith("material"):
            mat = obj.active_material
            if mat is None:
                if not create:
                    raise ValueError(f"{obj.name} has no material")
                mat = bpy.data.materials.new(f"{obj.name}_Mat")
                if hasattr(mat, "use_nodes"):
                    mat.use_nodes = True
                obj.data.materials.append(mat)
            if hasattr(mat, "use_nodes") and not mat.use_nodes:
                mat.use_nodes = True
            return mat.node_tree, f"material {mat.name} on {obj.name}", obj
        if kind.startswith("geonodes"):
            modname = kind.split(":", 1)[1] if ":" in kind else None
            mod = None
            for m in obj.modifiers:
                if m.type == "NODES" and (modname is None or m.name == modname):
                    mod = m
                    break
            if mod is None:
                if not create:
                    raise ValueError(f"{obj.name} has no Geometry Nodes modifier")
                mod = obj.modifiers.new(modname or "GeometryNodes", "NODES")
            if mod.node_group is None:
                if not create:
                    raise ValueError("modifier has no node group")
                ng = bpy.data.node_groups.new(f"{obj.name}_GN", "GeometryNodeTree")
                ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
                ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
                gi = ng.nodes.new("NodeGroupInput"); gi.location = (-400, 0)
                go = ng.nodes.new("NodeGroupOutput"); go.location = (400, 0)
                ng.links.new(gi.outputs[0], go.inputs[0])
                mod.node_group = ng
            return mod.node_group, f"geometry nodes {mod.node_group.name} (modifier {mod.name} on {obj.name})", obj
        raise ValueError("object target kind must be 'material' or 'geonodes[:ModifierName]'")
    if t.startswith(("group:", "geometry_group:", "shader_group:", "compositor_group:")):
        kind, name = t.split(":", 1)
        ng = bpy.data.node_groups.get(name)
        if ng is None:
            if not create or kind == "group":
                raise ValueError(f"Node group not found: {name}")
            tree_type = {"geometry_group": "GeometryNodeTree", "shader_group": "ShaderNodeTree",
                         "compositor_group": "CompositorNodeTree"}[kind]
            ng = bpy.data.node_groups.new(name, tree_type)
        return ng, f"node group {ng.name} ({ng.bl_idname})", None
    if t == "world" or t.startswith("world:"):
        scene = bpy.context.scene
        name = t.split(":", 1)[1] if ":" in t else None
        world = bpy.data.worlds.get(name) if name else scene.world
        if world is None:
            if not create:
                raise ValueError("No world")
            world = bpy.data.worlds.new(name or "World")
            scene.world = world
        if hasattr(world, "use_nodes") and not world.use_nodes:
            world.use_nodes = True
        return world.node_tree, f"world {world.name}", None
    if t == "compositor":
        scene = bpy.context.scene
        if hasattr(scene, "compositing_node_group"):
            if scene.compositing_node_group is None:
                if not create:
                    raise ValueError("No compositor node group")
                scene.compositing_node_group = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
            return scene.compositing_node_group, "compositor", None
        scene.use_nodes = True
        return scene.node_tree, "compositor", None
    raise ValueError("target must be one of: material:<Name>, object:<Name>/material, object:<Name>/geonodes[:Mod], "
                     "group:<Name>, geometry_group:<Name>, shader_group:<Name>, world[:Name], compositor")


# ---------------------------------------------------------------- sockets

def socket_key(sockets, s):
    """Stable reference: name if unique among enabled sockets, else index."""
    enabled = [x for x in sockets if x.enabled]
    same = [x for x in enabled if x.name == s.name]
    if len(same) == 1:
        return s.name
    return list(sockets).index(s)


def find_socket(node, key, outputs=False):
    sockets = node.outputs if outputs else node.inputs
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        idx = int(key)
        if idx < len(sockets):
            return sockets[idx]
    else:
        for pred in (lambda s: s.enabled and s.name == key,
                     lambda s: s.enabled and s.identifier == key,
                     lambda s: s.name == key or s.identifier == key,
                     lambda s: s.enabled and s.name.lower() == str(key).lower()):
            for s in sockets:
                if pred(s):
                    return s
    avail = [f"{i}:{s.name}({s.type})" for i, s in enumerate(sockets) if s.enabled]
    raise KeyError(f"{'output' if outputs else 'input'} socket {key!r} not found on node {node.name!r} "
                   f"({node.bl_idname}). Available: {', '.join(avail)}")


def socket_value(s):
    if not hasattr(s, "default_value"):
        return None
    try:
        return jsonable(s.default_value)
    except Exception:
        return None


def set_socket_value(s, v):
    if s.type in {"OBJECT", "MATERIAL", "COLLECTION", "IMAGE", "TEXTURE"} and isinstance(v, str):
        fixed = {"OBJECT": "Object", "MATERIAL": "Material", "COLLECTION": "Collection",
                 "IMAGE": "Image", "TEXTURE": "Texture"}[s.type]
        s.default_value = lookup_id(fixed, v)
        return
    if s.type == "RGBA" and isinstance(v, (list, tuple)) and len(v) == 3:
        v = list(v) + [1.0]
    if s.type == "RGBA" and isinstance(v, str) and v.startswith("#"):
        h = v.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        v = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb] + [1.0]  # sRGB->linear
    s.default_value = v


# ---------------------------------------------------------------- read

def _node_props(node, defaults_node=None):
    out = {}
    for p in node.bl_rna.properties:
        pid = p.identifier
        if pid in _BASE_NODE_PROPS or p.is_readonly and p.type != "POINTER":
            continue
        try:
            val = getattr(node, pid)
        except Exception:
            continue
        if p.type == "POINTER":
            if pid == "color_ramp" and val is not None:
                out["color_ramp"] = {"interpolation": val.interpolation, "color_mode": val.color_mode,
                                     "elements": [[r(e.position, 3), r(list(e.color), 3)] for e in val.elements]}
            elif pid == "mapping" and val is not None and hasattr(val, "curves"):
                out["mapping"] = [[r(list(pt.location), 3) for pt in c.points] for c in val.curves]
            elif isinstance(val, bpy.types.ID):
                out[pid] = val.name
            continue
        if p.type == "COLLECTION":
            continue
        if defaults_node is not None:
            try:
                dv = getattr(defaults_node, pid)
                if jsonable(dv) == jsonable(val):
                    continue
            except Exception:
                pass
        out[pid] = jsonable(val)
    return out


def get_tree(target, include_defaults=False):
    tree, desc, _ = resolve_tree(target, create=False)
    scratch = None
    if not include_defaults:
        try:
            scratch = bpy.data.node_groups.new(".blendpro_scratch", tree.bl_idname)
        except Exception:
            scratch = None
    try:
        nodes = []
        for n in tree.nodes:
            twin = None
            if scratch is not None and n.bl_idname not in {"NodeGroupInput", "NodeGroupOutput", "NodeFrame", "NodeReroute"}:
                try:
                    twin = scratch.nodes.new(n.bl_idname)
                except Exception:
                    twin = None
            props = _node_props(n, twin)
            if twin is not None:
                for k, v in props.items():  # match socket layout (e.g. data_type) before comparing defaults
                    try:
                        if not isinstance(v, (dict, list)) or k not in {"color_ramp", "mapping"}:
                            setattr(twin, k, getattr(n, k))
                    except Exception:
                        pass
            nd = {"name": n.name, "type": n.bl_idname}
            if n.label:
                nd["label"] = n.label
            nd["loc"] = [round(n.location.x), round(n.location.y)]
            if n.parent:
                nd["parent"] = n.parent.name
            if n.mute:
                nd["mute"] = True
            if props:
                nd["props"] = props
            inputs = {}
            for i, s in enumerate(n.inputs):
                if not s.enabled or s.is_linked:
                    continue
                v = socket_value(s)
                if v is None:
                    continue
                if twin is not None and i < len(twin.inputs):
                    if socket_value(twin.inputs[i]) == v:
                        continue
                inputs[str(socket_key(n.inputs, s))] = v
            if inputs:
                nd["inputs"] = inputs
            nodes.append(nd)
        links = []
        for l in tree.links:
            if not l.is_valid:
                bad = True
            else:
                bad = False
            links.append([l.from_node.name, socket_key(l.from_node.outputs, l.from_socket),
                          l.to_node.name, socket_key(l.to_node.inputs, l.to_socket)] + (["INVALID"] if bad else []))
        res = {"target": desc, "tree": tree.name, "tree_type": tree.bl_idname, "nodes": nodes, "links": links}
        if hasattr(tree, "interface") and tree.interface:
            iface = []
            for item in tree.interface.items_tree:
                if getattr(item, "item_type", "") == "SOCKET":
                    e = {"name": item.name, "in_out": item.in_out, "socket_type": item.socket_type}
                    if hasattr(item, "default_value"):
                        e["default"] = jsonable(item.default_value)
                    iface.append(e)
            if iface:
                res["interface"] = iface
        return res
    finally:
        if scratch is not None:
            bpy.data.node_groups.remove(scratch)


# ---------------------------------------------------------------- write

def _node_type_suggestions(type_name, tree):
    pool = []
    for cls_name in dir(bpy.types):
        cls = getattr(bpy.types, cls_name)
        try:
            if issubclass(cls, bpy.types.Node) and cls_name not in ("Node", "ShaderNode", "GeometryNode", "CompositorNode"):
                pool.append(cls_name)
        except TypeError:
            pass
    prefix = {"ShaderNodeTree": ("ShaderNode",), "GeometryNodeTree": ("GeometryNode", "FunctionNode", "ShaderNode"),
              "CompositorNodeTree": ("CompositorNode",)}.get(tree.bl_idname, ("",))
    pool = [p for p in pool if p.startswith(prefix) or p.startswith("Node")]
    return difflib.get_close_matches(type_name, pool, n=6, cutoff=0.4)


def _set_prop(node, key, val):
    if key == "color_ramp":
        cr = node.color_ramp
        if "interpolation" in val:
            cr.interpolation = val["interpolation"]
        if "color_mode" in val:
            cr.color_mode = val["color_mode"]
        els = val.get("elements")
        if els:
            while len(cr.elements) > 1:
                cr.elements.remove(cr.elements[-1])
            cr.elements[0].position = els[0][0]
            cr.elements[0].color = (list(els[0][1]) + [1.0])[:4]
            for pos, col in els[1:]:
                e = cr.elements.new(pos)
                e.color = (list(col) + [1.0])[:4]
        return
    if key == "mapping":
        curves = node.mapping.curves
        for ci, pts in enumerate(val):
            if ci >= len(curves):
                break
            c = curves[ci]
            while len(c.points) > 2:
                c.points.remove(c.points[-1])
            for pi, (x, y) in enumerate(pts):
                if pi < len(c.points):
                    c.points[pi].location = (x, y)
                else:
                    c.points.new(x, y)
        node.mapping.update()
        return
    p = node.bl_rna.properties.get(key)
    if p is None:
        avail = [q.identifier for q in node.bl_rna.properties if q.identifier not in _BASE_NODE_PROPS and not q.is_readonly]
        raise KeyError(f"node {node.name!r} ({node.bl_idname}) has no property {key!r}. Settable: {', '.join(avail)}")
    if p.type == "POINTER" and isinstance(val, str):
        val = lookup_id(p.fixed_type.identifier, val)
    if p.type == "ENUM" and isinstance(val, str) and not p.is_enum_flag:
        ids = [e.identifier for e in p.enum_items]
        if ids and val not in ids:
            up = val.upper()
            if up in ids:
                val = up
            else:
                raise ValueError(f"{node.name}.{key}={val!r} invalid. Options: {ids}")
    setattr(node, key, val)


def build_tree(target, nodes=None, links=None, remove=None, unlink=None, clear=False, interface=None,
               auto_layout=True):
    tree, desc, owner = resolve_tree(target, create=True)
    errors, created, updated = [], [], []
    keep_types = {"NodeGroupInput", "NodeGroupOutput"}
    if clear:
        for n in list(tree.nodes):
            if n.bl_idname not in keep_types:
                tree.nodes.remove(n)
    # interface sockets (node groups / geometry nodes)
    if interface:
        for item in interface:
            try:
                existing = next((it for it in tree.interface.items_tree if getattr(it, "item_type", "") == "SOCKET"
                                 and it.name == item["name"] and it.in_out == item.get("in_out", "INPUT")), None)
                sock = existing or tree.interface.new_socket(item["name"], in_out=item.get("in_out", "INPUT"),
                                                             socket_type=item.get("socket_type", "NodeSocketFloat"))
                for k in ("default_value", "min_value", "max_value", "description", "subtype"):
                    if k in item:
                        setattr(sock, k, item[k])
            except Exception as e:
                errors.append(f"interface {item}: {e}")
    for name in remove or []:
        n = tree.nodes.get(name)
        if n:
            tree.nodes.remove(n)
        else:
            errors.append(f"remove: node {name!r} not found")
    for ul in unlink or []:
        try:
            fn, fs, tn, ts = ul
            to_sock = find_socket(tree.nodes[tn], ts)
            for l in list(to_sock.links):
                if l.from_node.name == fn:
                    tree.links.remove(l)
        except Exception as e:
            errors.append(f"unlink {ul}: {e}")
    new_nodes = []
    for spec in nodes or []:
        name = spec.get("name")
        n = tree.nodes.get(name) if name else None
        try:
            if n is None:
                ntype = spec.get("type")
                if not ntype:
                    raise ValueError("new node needs 'type' (bl_idname, e.g. ShaderNodeBsdfPrincipled)")
                if ntype in {"NodeGroupInput", "NodeGroupOutput"}:
                    n = next((x for x in tree.nodes if x.bl_idname == ntype), None)
                if n is None:
                    try:
                        n = tree.nodes.new(ntype)
                    except RuntimeError:
                        sug = _node_type_suggestions(ntype, tree)
                        raise ValueError(f"unknown node type {ntype!r} for {tree.bl_idname}. Close matches: {sug}")
                if name:
                    n.name = name
                created.append(n.name)
                new_nodes.append(n)
            else:
                updated.append(n.name)
            if "label" in spec:
                n.label = spec["label"]
            if "loc" in spec or "location" in spec:
                n.location = spec.get("loc") or spec.get("location")
            elif n in new_nodes:
                n["_bp_autolayout"] = 1
            if "mute" in spec:
                n.mute = bool(spec["mute"])
            for k, v in (spec.get("props") or {}).items():
                try:
                    _set_prop(n, k, v)
                except Exception as e:
                    errors.append(f"{n.name}.props.{k}: {e}")
            for k, v in (spec.get("inputs") or {}).items():
                try:
                    set_socket_value(find_socket(n, k), v)
                except Exception as e:
                    errors.append(f"{n.name}.inputs[{k!r}]: {e}")
            if "parent" in spec and spec["parent"]:
                n.parent = tree.nodes[spec["parent"]]
        except Exception as e:
            errors.append(f"node {spec.get('name') or spec.get('type')}: {e}")
    nlinks = 0
    for ln in links or []:
        try:
            if isinstance(ln, str):
                # "From.Socket -> To.Socket"
                a, b = [x.strip() for x in ln.split("->")]
                fn, fs = a.rsplit(".", 1) if "." in a else (a, 0)
                tn, ts = b.rsplit(".", 1) if "." in b else (b, 0)
            else:
                fn, fs, tn, ts = ln[:4]
            fnode = tree.nodes.get(fn)
            tnode = tree.nodes.get(tn)
            if fnode is None or tnode is None:
                missing = fn if fnode is None else tn
                raise KeyError(f"node {missing!r} not found. Nodes: {[x.name for x in tree.nodes]}")
            link = tree.links.new(find_socket(fnode, fs, outputs=True), find_socket(tnode, ts))
            if not link.is_valid:
                errors.append(f"link {ln}: created but INVALID (incompatible socket types)")
            nlinks += 1
        except Exception as e:
            errors.append(f"link {ln}: {e}")
    if auto_layout:
        _auto_layout(tree, only_flagged=not clear)
    for n in tree.nodes:
        if "_bp_autolayout" in n:
            del n["_bp_autolayout"]
    res = {"target": desc, "tree": tree.name, "created": created, "updated": updated, "links_added": nlinks,
           "node_count": len(tree.nodes)}
    if errors:
        res["errors"] = errors
    if owner is not None and tree.bl_idname == "GeometryNodeTree":
        res["evaluated"] = evaluated_stats(owner)
    return res


def evaluated_stats(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    st = {}
    try:
        me = ev.to_mesh()
        st = {"verts": len(me.vertices), "faces": len(me.polygons)}
        ev.to_mesh_clear()
    except Exception:
        pass
    inst = 0
    for di in dg.object_instances:
        if di.is_instance and di.parent and di.parent.original == obj:
            inst += 1
    if inst:
        st["instances"] = inst
    try:
        st["dimensions"] = r(list(ev.dimensions), 3)
    except Exception:
        pass
    return st


def _auto_layout(tree, only_flagged=True):
    nodes = list(tree.nodes)
    if not nodes:
        return
    targets = [n for n in nodes if (not only_flagged) or "_bp_autolayout" in n]
    if not targets:
        return
    # depth = longest path to a sink (output) node
    out_links = {}
    for l in tree.links:
        out_links.setdefault(l.from_node.name, []).append(l.to_node.name)
    memo = {}

    def depth(name, stack=()):
        if name in memo:
            return memo[name]
        if name in stack:
            return 0
        d = 0
        for nxt in out_links.get(name, []):
            d = max(d, 1 + depth(nxt, stack + (name,)))
        memo[name] = d
        return d

    anchor_x = max((n.location.x for n in nodes if n not in targets), default=0.0)
    if not only_flagged:
        anchor_x = 300.0
    cols = {}
    for n in targets:
        cols.setdefault(depth(n.name), []).append(n)
    for d, col in cols.items():
        for i, n in enumerate(col):
            n.location = (anchor_x - 260 * d, 200 - 220 * i)
