"""Geometry Nodes procedural recipes + scene/pipeline recipes for XR delivery (Unity, Quest, Vision Pro, WebXR).

GN recipes build their tree with nodes.build_tree and expose key values as group-interface inputs, so the
agent can tweak them later on the modifier (inspect the modifier / node_tree_get for socket names).
Pipeline recipes never depend on selection and keep originals unless told otherwise."""
import math
import os
import tempfile

import bmesh
import bpy
import mathutils

from . import recipe
from ..util import get_obj, find_view3d, r, world_bbox
from ..nodes import build_tree, evaluated_stats

TAU = 6.283185307179586
_EXPORT_DIR = os.path.join(tempfile.gettempdir(), "blendpro_export")


# ============================================================== helpers

def _coll():
    return bpy.context.view_layer.active_layer_collection.collection


def _link(obj, coll=None):
    (coll or _coll()).objects.link(obj)
    return obj


def _demo_mesh(name, kind="cube", size=1.0, location=(0, 0, 0), segments=(32, 16), cuts=0):
    """Create a mesh object via bmesh (no operators, no selection changes)."""
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    if kind == "cube":
        bmesh.ops.create_cube(bm, size=size, calc_uvs=True)
        if cuts:
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    elif kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=max(1, segments[0]), y_segments=max(1, segments[1]), size=size / 2,
                              calc_uvs=True)
    elif kind == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=segments[0], v_segments=segments[1], radius=size / 2, calc_uvs=True)
    elif kind == "ico":
        bmesh.ops.create_icosphere(bm, subdivisions=max(1, segments[0]), radius=size / 2, calc_uvs=True)
    else:
        raise ValueError("kind must be cube|plane|sphere|ico")
    del uv
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    _link(obj)
    bpy.context.view_layer.update()  # matrix_world is only valid after an update
    return obj


def _demo_curve(name="DemoCurve", points=((-4, 0, 0), (-1.5, 2, 0), (1.5, -2, 0), (4, 0, 0))):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(points) - 1)
    for bp, co in zip(sp.bezier_points, points):
        bp.co = co
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    return _link(bpy.data.objects.new(name, cu))


def _empty_mesh_obj(name, location=(0, 0, 0)):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj.location = location
    return _link(obj)


def _mat(name, color=(0.8, 0.8, 0.8, 1)):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.diffuse_color = color
    if hasattr(m, "use_nodes") and not m.use_nodes:
        m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF") if m.node_tree else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
    return m


_SOCK = {"FLOAT": "NodeSocketFloat", "INT": "NodeSocketInt", "BOOL": "NodeSocketBool", "VECTOR": "NodeSocketVector",
         "OBJECT": "NodeSocketObject", "COLLECTION": "NodeSocketCollection", "MATERIAL": "NodeSocketMaterial"}


def _I(name, kind, value=None, mn=None, mx=None):
    return (name, kind, value, mn, mx)


def _set_mod_input(mod, identifier, value):
    """Blender 5.x exposes GN modifier inputs as RNA (mod.properties.inputs.Socket_N); older builds use ID props."""
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs") and hasattr(props.inputs, identifier):
        getattr(props.inputs, identifier).value = value
    else:
        mod[identifier] = value


def _gn(obj, mod_name, inputs, nodes, links, out_name="Geometry"):
    """(Re)build a GN modifier `mod_name` on obj. inputs: list of _I(...) interface specs; values are written
    both as socket defaults and on the modifier. Raises on any build error. Returns compact stats."""
    old = obj.modifiers.get(mod_name)
    if old is not None:
        ng_old = old.node_group
        obj.modifiers.remove(old)
        if ng_old is not None and ng_old.users == 0:
            bpy.data.node_groups.remove(ng_old)
    iface = []
    for name, kind, value, mn, mx in inputs:
        it = {"name": name, "socket_type": _SOCK[kind]}
        if kind in ("FLOAT", "INT", "BOOL", "VECTOR") and value is not None:
            it["default_value"] = tuple(value) if kind == "VECTOR" else value
        if mn is not None:
            it["min_value"] = mn
        if mx is not None:
            it["max_value"] = mx
        iface.append(it)
    res = build_tree(f"object:{obj.name}/geonodes:{mod_name}", nodes=nodes, links=links, interface=iface, clear=True)
    if res.get("errors"):
        raise RuntimeError(f"GN build errors: {res['errors'][:6]}")
    mod = obj.modifiers[mod_name]
    ng = mod.node_group
    ng.name = f"BP_{mod_name}_{obj.name}"
    ids = {it.name: it.identifier for it in ng.interface.items_tree
           if getattr(it, "item_type", "") == "SOCKET" and it.in_out == "INPUT"}
    for name, kind, value, _, _ in inputs:
        if value is None:
            continue
        if kind in ("OBJECT", "COLLECTION", "MATERIAL") and isinstance(value, str):
            value = {"OBJECT": bpy.data.objects, "COLLECTION": bpy.data.collections,
                     "MATERIAL": bpy.data.materials}[kind][value]
        _set_mod_input(mod, ids[name], tuple(value) if kind == "VECTOR" else value)
    obj.update_tag()
    bpy.context.view_layer.update()
    return {"object": obj.name, "modifier": mod_name, "node_group": ng.name,
            "inputs": [i[0] for i in inputs], "evaluated": evaluated_stats(obj)}


def _mesh_from_eval(obj):
    """New mesh datablock with all modifiers/GN applied (object local space)."""
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    return bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)


def _tris(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


def _eval_tris(obj):
    me = _mesh_from_eval(obj)
    try:
        return _tris(me)
    finally:
        bpy.data.meshes.remove(me)


def _meshes(names=None, allow_demo=None):
    if names:
        if isinstance(names, str):
            names = [names]
        objs = [get_obj(n) for n in names]
        bad = [o.name for o in objs if o.type != "MESH"]
        if bad:
            raise ValueError(f"not mesh objects: {bad}")
        return objs
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objs and allow_demo:
        objs = allow_demo()
    bpy.context.view_layer.update()
    return objs


def _target(target, make_demo, types=("MESH",)):
    if target is None:
        return make_demo(), True
    obj = get_obj(target)
    if obj.type not in types:
        raise ValueError(f"{obj.name} is {obj.type}; expected {types}")
    return obj, False


def _base_name(n):
    import re
    n = re.sub(r"\.\d{3}$", "", n)
    return re.sub(r"_LOD\d+$", "", n)


# ============================================================== GEOMETRY NODES

def _scatter(poisson, target, instance, collection, density, min_distance, seed, scale_min, scale_max,
             align_to_normal, random_rotation, vertex_group, keep_surface, modifier):
    def demo():
        return _demo_mesh("ScatterGround", "plane", size=10, segments=(10, 10))
    obj, demo_made = _target(target, demo)
    if instance is None and collection is None:
        inst = _demo_mesh("ScatterRock", "ico", size=0.3, segments=(1, 1), location=(0, 0, -3))
        inst.hide_render = True
        instance = inst.name
    if instance is not None:
        get_obj(instance)
    if collection is not None and collection not in bpy.data.collections:
        raise ValueError(f"collection {collection!r} not found: {list(bpy.data.collections.keys())[:20]}")
    if vertex_group and vertex_group not in obj.vertex_groups:
        raise ValueError(f"vertex group {vertex_group!r} not on {obj.name}: {[g.name for g in obj.vertex_groups]}")
    inputs = [_I("Seed", "INT", int(seed)), _I("Scale Min", "FLOAT", scale_min, 0.0),
              _I("Scale Max", "FLOAT", scale_max, 0.0), _I("Align to Normal", "BOOL", bool(align_to_normal)),
              _I("Random Z Rotation", "FLOAT", TAU if random_rotation else 0.0, 0.0, TAU)]
    if poisson:
        inputs += [_I("Min Distance", "FLOAT", min_distance, 0.0), _I("Density Max", "FLOAT", density, 0.0)]
    else:
        inputs += [_I("Density", "FLOAT", density, 0.0)]
    if collection is not None:
        inputs.append(_I("Instance Collection", "COLLECTION", collection))
        info = {"name": "Info", "type": "GeometryNodeCollectionInfo",
                "inputs": {"Separate Children": True, "Reset Children": True}}
        info_links = [["Group Input", "Instance Collection", "Info", "Collection"], ["Info", "Instances", "IOP", "Instance"]]
    else:
        inputs.append(_I("Instance Object", "OBJECT", instance))
        info = {"name": "Info", "type": "GeometryNodeObjectInfo", "inputs": {"As Instance": True}}
        info_links = [["Group Input", "Instance Object", "Info", "Object"], ["Info", "Geometry", "IOP", "Instance"]]
    nodes = [
        {"name": "Dist", "type": "GeometryNodeDistributePointsOnFaces",
         "props": {"distribute_method": "POISSON" if poisson else "RANDOM"}},
        {"name": "RotSw", "type": "GeometryNodeSwitch", "props": {"input_type": "ROTATION"}},
        {"name": "RandRot", "type": "FunctionNodeRandomValue", "props": {"data_type": "FLOAT"}, "inputs": {"Min": 0.0}},
        {"name": "Eul", "type": "ShaderNodeCombineXYZ"},
        {"name": "E2R", "type": "FunctionNodeEulerToRotation"},
        {"name": "RotR", "type": "FunctionNodeRotateRotation", "props": {"rotation_space": "LOCAL"}},
        {"name": "SeedS", "type": "ShaderNodeMath", "props": {"operation": "ADD"}, "inputs": {"1": 7.0}},
        {"name": "RandS", "type": "FunctionNodeRandomValue", "props": {"data_type": "FLOAT"}},
        info,
        {"name": "IOP", "type": "GeometryNodeInstanceOnPoints",
         "inputs": {"Pick Instance": collection is not None}},
        {"name": "Join", "type": "GeometryNodeJoinGeometry"},
    ]
    links = [
        ["Group Input", "Geometry", "Dist", "Mesh"], ["Group Input", "Seed", "Dist", "Seed"],
        ["Dist", "Rotation", "RotSw", "True"], ["Group Input", "Align to Normal", "RotSw", "Switch"],
        ["Group Input", "Random Z Rotation", "RandRot", "Max"], ["Group Input", "Seed", "RandRot", "Seed"],
        ["RandRot", "Value", "Eul", "Z"], ["Eul", "Vector", "E2R", "Euler"],
        ["RotSw", "Output", "RotR", "Rotation"], ["E2R", "Rotation", "RotR", "Rotate By"],
        ["Group Input", "Seed", "SeedS", 0], ["SeedS", "Value", "RandS", "Seed"],
        ["Group Input", "Scale Min", "RandS", "Min"], ["Group Input", "Scale Max", "RandS", "Max"],
        ["Dist", "Points", "IOP", "Points"], ["RotR", "Rotation", "IOP", "Rotation"], ["RandS", "Value", "IOP", "Scale"],
        ["IOP", "Instances", "Join", "Geometry"], ["Join", "Geometry", "Group Output", "Geometry"],
    ] + info_links
    if keep_surface:
        links.insert(0, ["Group Input", "Geometry", "Join", "Geometry"])
    dens_sock = "Density Factor" if poisson else "Density"
    if poisson:
        links += [["Group Input", "Min Distance", "Dist", "Distance Min"], ["Group Input", "Density Max", "Dist", "Density Max"]]
    if vertex_group:
        nodes += [{"name": "VG", "type": "GeometryNodeInputNamedAttribute", "props": {"data_type": "FLOAT"},
                   "inputs": {"Name": vertex_group}}]
        if poisson:
            links.append(["VG", "Attribute", "Dist", dens_sock])
        else:
            nodes.append({"name": "Mask", "type": "ShaderNodeMath", "props": {"operation": "MULTIPLY"}})
            links += [["Group Input", "Density", "Mask", 0], ["VG", "Attribute", "Mask", 1], ["Mask", "Value", "Dist", "Density"]]
    elif not poisson:
        links.append(["Group Input", "Density", "Dist", "Density"])
    out = _gn(obj, modifier, inputs, nodes, links)
    out.update({"instance": instance, "collection": collection, "demo": demo_made})
    return out


@recipe("geonodes", tags=("scatter", "instances", "foliage", "props"))
def scatter_on_surface(target=None, instance=None, collection=None, density=20.0, seed=0, scale_min=0.6,
                       scale_max=1.2, align_to_normal=True, random_rotation=True, vertex_group=None,
                       keep_surface=True, modifier="BP_Scatter") -> dict:
    '''Scatter instances (object or collection) randomly over a mesh with a Geometry Nodes modifier.
    density = points per m2; vertex_group multiplies density by weight (paint a mask). collection picks a random child
    per point. Instances stay instances (cheap) - use realize_and_apply_gn to bake for export.
    target=None builds a demo 10m ground plane + ico rock. Exposed inputs: Density, Seed, Scale Min/Max, Align to Normal,
    Random Z Rotation (rad), Instance Object/Collection.'''
    return _scatter(False, target, instance, collection, density, None, seed, scale_min, scale_max,
                    align_to_normal, random_rotation, vertex_group, keep_surface, modifier)


@recipe("geonodes", tags=("scatter", "poisson", "instances", "min-distance"))
def scatter_with_min_distance(target=None, instance=None, collection=None, min_distance=0.5, density_max=50.0,
                              seed=0, scale_min=0.8, scale_max=1.2, align_to_normal=False, random_rotation=True,
                              vertex_group=None, keep_surface=True, modifier="BP_ScatterPoisson") -> dict:
    '''Poisson-disk scatter: instances never closer than min_distance (trees, rocks, crowd spots, no overlaps).
    density_max caps candidate points per m2 (raise it if coverage is sparse); vertex_group weights act as density factor.
    target=None builds a demo plane + rock. Exposed inputs: Min Distance, Density Max, Seed, Scale Min/Max, Align to Normal,
    Random Z Rotation, Instance Object/Collection.'''
    return _scatter(True, target, instance, collection, density_max, min_distance, seed, scale_min, scale_max,
                    align_to_normal, random_rotation, vertex_group, keep_surface, modifier)


@recipe("geonodes", tags=("curve", "instances", "array", "path"))
def instance_along_curve(curve=None, instance=None, spacing=0.5, align=True, scale=1.0, keep_curve=False,
                         modifier="BP_AlongCurve") -> dict:
    '''Place instances of an object every `spacing` metres along a curve object (posts, lamps, chain links, bricks).
    align=True orients each instance to the curve's tangent frame (Curve to Points rotation).
    curve=None builds a demo S-curve; instance=None builds a demo cube. Exposed inputs: Spacing, Align, Scale, Instance.'''
    obj, demo = _target(curve, _demo_curve, types=("CURVE",))
    if instance is None:
        instance = _demo_mesh("CurveItem", "cube", size=0.2, location=(0, 0, -3)).name
    get_obj(instance)
    inputs = [_I("Spacing", "FLOAT", spacing, 0.001), _I("Align", "BOOL", bool(align)), _I("Scale", "FLOAT", scale, 0.0),
              _I("Instance", "OBJECT", instance)]
    nodes = [{"name": "CTP", "type": "GeometryNodeCurveToPoints", "props": {"mode": "LENGTH"}},
             {"name": "RotSw", "type": "GeometryNodeSwitch", "props": {"input_type": "ROTATION"}},
             {"name": "Info", "type": "GeometryNodeObjectInfo", "inputs": {"As Instance": True}},
             {"name": "IOP", "type": "GeometryNodeInstanceOnPoints"},
             {"name": "Join", "type": "GeometryNodeJoinGeometry"}]
    links = [["Group Input", "Geometry", "CTP", "Curve"], ["Group Input", "Spacing", "CTP", "Length"],
             ["CTP", "Rotation", "RotSw", "True"], ["Group Input", "Align", "RotSw", "Switch"],
             ["Group Input", "Instance", "Info", "Object"],
             ["CTP", "Points", "IOP", "Points"], ["Info", "Geometry", "IOP", "Instance"],
             ["RotSw", "Output", "IOP", "Rotation"], ["Group Input", "Scale", "IOP", "Scale"],
             ["IOP", "Instances", "Join", "Geometry"], ["Join", "Geometry", "Group Output", "Geometry"]]
    if keep_curve:
        links.insert(0, ["Group Input", "Geometry", "Join", "Geometry"])
    out = _gn(obj, modifier, inputs, nodes, links)
    out.update({"instance": instance, "demo": demo})
    return out


@recipe("geonodes", tags=("curve", "fence", "railing", "architecture"))
def procedural_fence(curve=None, post_spacing=1.5, post_height=1.1, post_width=0.08, rail_count=2,
                     rail_radius=0.025, modifier="BP_Fence") -> dict:
    '''Fence/railing along a curve: box posts every post_spacing m + round rails following the curve.
    rail_count is baked into the tree (re-run to change it); rails sit at 0.9*height*i/rail_count.
    curve=None builds a demo S-curve. Exposed inputs: Post Spacing, Post Height, Post Width, Rail Radius.'''
    obj, demo = _target(curve, _demo_curve, types=("CURVE",))
    rail_count = max(1, int(rail_count))
    inputs = [_I("Post Spacing", "FLOAT", post_spacing, 0.01), _I("Post Height", "FLOAT", post_height, 0.0),
              _I("Post Width", "FLOAT", post_width, 0.0), _I("Rail Radius", "FLOAT", rail_radius, 0.0)]
    nodes = [{"name": "CTP", "type": "GeometryNodeCurveToPoints", "props": {"mode": "LENGTH"}},
             {"name": "PSize", "type": "ShaderNodeCombineXYZ"},
             {"name": "Post", "type": "GeometryNodeMeshCube"},
             {"name": "HalfH", "type": "ShaderNodeMath", "props": {"operation": "MULTIPLY"}, "inputs": {"1": 0.5}},
             {"name": "PUp", "type": "ShaderNodeCombineXYZ"},
             {"name": "PostT", "type": "GeometryNodeTransform"},
             {"name": "IOP", "type": "GeometryNodeInstanceOnPoints"},
             {"name": "Real", "type": "GeometryNodeRealizeInstances"},
             {"name": "RailJoin", "type": "GeometryNodeJoinGeometry"},
             {"name": "Prof", "type": "GeometryNodeCurvePrimitiveCircle", "inputs": {"Resolution": 8}},
             {"name": "C2M", "type": "GeometryNodeCurveToMesh", "inputs": {"Fill Caps": True}},
             {"name": "Join", "type": "GeometryNodeJoinGeometry"}]
    links = [["Group Input", "Geometry", "CTP", "Curve"], ["Group Input", "Post Spacing", "CTP", "Length"],
             ["Group Input", "Post Width", "PSize", "X"], ["Group Input", "Post Width", "PSize", "Y"],
             ["Group Input", "Post Height", "PSize", "Z"], ["PSize", "Vector", "Post", "Size"],
             ["Group Input", "Post Height", "HalfH", 0], ["HalfH", "Value", "PUp", "Z"],
             ["Post", "Mesh", "PostT", "Geometry"], ["PUp", "Vector", "PostT", "Translation"],
             ["CTP", "Points", "IOP", "Points"], ["PostT", "Geometry", "IOP", "Instance"],
             ["CTP", "Rotation", "IOP", "Rotation"], ["IOP", "Instances", "Real", "Geometry"],
             ["Group Input", "Rail Radius", "Prof", "Radius"],
             ["RailJoin", "Geometry", "C2M", "Curve"], ["Prof", "Curve", "C2M", "Profile Curve"],
             ["Real", "Geometry", "Join", "Geometry"], ["C2M", "Mesh", "Join", "Geometry"],
             ["Join", "Geometry", "Group Output", "Geometry"]]
    for i in range(1, rail_count + 1):
        nodes += [{"name": f"RH{i}", "type": "ShaderNodeMath", "props": {"operation": "MULTIPLY"},
                   "inputs": {"1": 0.9 * i / rail_count}},
                  {"name": f"RV{i}", "type": "ShaderNodeCombineXYZ"},
                  {"name": f"RT{i}", "type": "GeometryNodeTransform"}]
        links += [["Group Input", "Post Height", f"RH{i}", 0], [f"RH{i}", "Value", f"RV{i}", "Z"],
                  ["Group Input", "Geometry", f"RT{i}", "Geometry"], [f"RV{i}", "Vector", f"RT{i}", "Translation"],
                  [f"RT{i}", "Geometry", "RailJoin", "Geometry"]]
    out = _gn(obj, modifier, inputs, nodes, links)
    out.update({"rail_count": rail_count, "demo": demo})
    return out


@recipe("geonodes", tags=("building", "architecture", "windows", "blockout"))
def procedural_building(name="Building", floors=5, floor_height=3.0, width=10.0, depth=8.0, windows_x=6,
                        windows_y=5, window_inset=0.7, window_depth=0.15, location=(0, 0, 0),
                        modifier="BP_Building") -> dict:
    '''Procedural building blockout: box of floors x floor_height with an inset window grid on all side walls.
    Creates a new mesh object `name` (base at its origin) with wall/glass materials (M_Building_Wall/M_Building_Glass).
    Exposed inputs: Floors, Floor Height, Width, Depth, Windows X, Windows Y, Window Inset (0-1), Window Depth.
    Windows are one quad per floor per column - cheap for XR backdrops.'''
    obj = _empty_mesh_obj(name, location)
    wall = _mat("M_Building_Wall", (0.55, 0.52, 0.48, 1))
    glass = _mat("M_Building_Glass", (0.1, 0.18, 0.25, 1))
    inputs = [_I("Floors", "INT", int(floors), 1), _I("Floor Height", "FLOAT", floor_height, 0.1),
              _I("Width", "FLOAT", width, 0.1), _I("Depth", "FLOAT", depth, 0.1), _I("Windows X", "INT", int(windows_x), 1),
              _I("Windows Y", "INT", int(windows_y), 1), _I("Window Inset", "FLOAT", window_inset, 0.0, 1.0),
              _I("Window Depth", "FLOAT", window_depth, 0.0), _I("Wall Material", "MATERIAL", wall.name),
              _I("Glass Material", "MATERIAL", glass.name)]
    M = "ShaderNodeMath"
    nodes = [{"name": "F1", "type": M, "props": {"operation": "ADD"}, "inputs": {"1": 1.0}},
             {"name": "X1", "type": M, "props": {"operation": "ADD"}, "inputs": {"1": 1.0}},
             {"name": "Y1", "type": M, "props": {"operation": "ADD"}, "inputs": {"1": 1.0}},
             {"name": "Hgt", "type": M, "props": {"operation": "MULTIPLY"}},
             {"name": "Size", "type": "ShaderNodeCombineXYZ"},
             {"name": "Cube", "type": "GeometryNodeMeshCube"},
             {"name": "HalfH", "type": M, "props": {"operation": "MULTIPLY"}, "inputs": {"1": 0.5}},
             {"name": "Up", "type": "ShaderNodeCombineXYZ"},
             {"name": "Tr", "type": "GeometryNodeTransform"},
             {"name": "Nrm", "type": "GeometryNodeInputNormal"},
             {"name": "Sep", "type": "ShaderNodeSeparateXYZ"},
             {"name": "Abs", "type": M, "props": {"operation": "ABSOLUTE"}},
             {"name": "Side", "type": "FunctionNodeCompare", "props": {"data_type": "FLOAT", "operation": "LESS_THAN"},
              "inputs": {"B": 0.1}},
             {"name": "Ex1", "type": "GeometryNodeExtrudeMesh", "props": {"mode": "FACES"},
              "inputs": {"Offset Scale": 0.0, "Individual": True}},
             {"name": "Sc", "type": "GeometryNodeScaleElements", "props": {"domain": "FACE"}},
             {"name": "Neg", "type": M, "props": {"operation": "MULTIPLY"}, "inputs": {"1": -1.0}},
             {"name": "Ex2", "type": "GeometryNodeExtrudeMesh", "props": {"mode": "FACES"}, "inputs": {"Individual": True}},
             {"name": "MatW", "type": "GeometryNodeSetMaterial"},
             {"name": "MatG", "type": "GeometryNodeSetMaterial"}]
    links = [["Group Input", "Floors", "F1", 0], ["Group Input", "Windows X", "X1", 0], ["Group Input", "Windows Y", "Y1", 0],
             ["F1", "Value", "Cube", "Vertices Z"], ["X1", "Value", "Cube", "Vertices X"], ["Y1", "Value", "Cube", "Vertices Y"],
             ["Group Input", "Floors", "Hgt", 0], ["Group Input", "Floor Height", "Hgt", 1],
             ["Group Input", "Width", "Size", "X"], ["Group Input", "Depth", "Size", "Y"], ["Hgt", "Value", "Size", "Z"],
             ["Size", "Vector", "Cube", "Size"], ["Hgt", "Value", "HalfH", 0], ["HalfH", "Value", "Up", "Z"],
             ["Cube", "Mesh", "Tr", "Geometry"], ["Up", "Vector", "Tr", "Translation"],
             ["Nrm", "Normal", "Sep", "Vector"], ["Sep", "Z", "Abs", 0], ["Abs", "Value", "Side", "A"],
             ["Tr", "Geometry", "Ex1", "Mesh"], ["Side", "Result", "Ex1", "Selection"],
             ["Ex1", "Mesh", "Sc", "Geometry"], ["Ex1", "Top", "Sc", "Selection"], ["Group Input", "Window Inset", "Sc", "Scale"],
             ["Group Input", "Window Depth", "Neg", 0], ["Sc", "Geometry", "Ex2", "Mesh"], ["Ex1", "Top", "Ex2", "Selection"],
             ["Neg", "Value", "Ex2", "Offset Scale"],
             ["Ex2", "Mesh", "MatW", "Geometry"], ["Group Input", "Wall Material", "MatW", "Material"],
             ["MatW", "Geometry", "MatG", "Geometry"], ["Ex2", "Top", "MatG", "Selection"],
             ["Group Input", "Glass Material", "MatG", "Material"], ["MatG", "Geometry", "Group Output", "Geometry"]]
    return _gn(obj, modifier, inputs, nodes, links)


@recipe("geonodes", tags=("stairs", "architecture", "blockout"))
def procedural_stairs(name="Stairs", steps=12, step_height=0.18, step_depth=0.28, width=1.2, location=(0, 0, 0),
                      modifier="BP_Stairs") -> dict:
    '''Solid straight staircase (each step a box down to the floor) climbing along +Y from the object origin.
    Creates new mesh object `name`. Exposed inputs: Steps, Step Height, Step Depth, Width.
    Realized mesh -> exports directly; total rise = steps*step_height, run = steps*step_depth.'''
    obj = _empty_mesh_obj(name, location)
    inputs = [_I("Steps", "INT", int(steps), 1), _I("Step Height", "FLOAT", step_height, 0.01),
              _I("Step Depth", "FLOAT", step_depth, 0.01), _I("Width", "FLOAT", width, 0.01)]
    M = "ShaderNodeMath"
    nodes = [{"name": "HalfD", "type": M, "props": {"operation": "MULTIPLY"}, "inputs": {"1": 0.5}},
             {"name": "Start", "type": "ShaderNodeCombineXYZ"}, {"name": "Off", "type": "ShaderNodeCombineXYZ"},
             {"name": "Line", "type": "GeometryNodeMeshLine", "props": {"mode": "OFFSET", "count_mode": "TOTAL"}},
             {"name": "Cube", "type": "GeometryNodeMeshCube", "inputs": {"Size": [1, 1, 1]}},
             {"name": "CubeT", "type": "GeometryNodeTransform", "inputs": {"Translation": [0, 0, 0.5]}},
             {"name": "Idx", "type": "GeometryNodeInputIndex"},
             {"name": "Idx1", "type": M, "props": {"operation": "ADD"}, "inputs": {"1": 1.0}},
             {"name": "ZS", "type": M, "props": {"operation": "MULTIPLY"}},
             {"name": "Scl", "type": "ShaderNodeCombineXYZ"},
             {"name": "IOP", "type": "GeometryNodeInstanceOnPoints"},
             {"name": "Real", "type": "GeometryNodeRealizeInstances"}]
    links = [["Group Input", "Step Depth", "HalfD", 0], ["HalfD", "Value", "Start", "Y"],
             ["Group Input", "Step Depth", "Off", "Y"], ["Group Input", "Steps", "Line", "Count"],
             ["Start", "Vector", "Line", "Start Location"], ["Off", "Vector", "Line", "Offset"],
             ["Cube", "Mesh", "CubeT", "Geometry"], ["Idx", "Index", "Idx1", 0], ["Idx1", "Value", "ZS", 0],
             ["Group Input", "Step Height", "ZS", 1], ["Group Input", "Width", "Scl", "X"],
             ["Group Input", "Step Depth", "Scl", "Y"], ["ZS", "Value", "Scl", "Z"],
             ["Line", "Mesh", "IOP", "Points"], ["CubeT", "Geometry", "IOP", "Instance"], ["Scl", "Vector", "IOP", "Scale"],
             ["IOP", "Instances", "Real", "Geometry"], ["Real", "Geometry", "Group Output", "Geometry"]]
    return _gn(obj, modifier, inputs, nodes, links)


@recipe("geonodes", tags=("cable", "wire", "catenary", "curve"))
def cable_between_points(start=(0, 0, 2), end=(4, 0, 2), sag=0.5, radius=0.02, resolution=32, name="Cable",
                         modifier="BP_Cable") -> dict:
    '''Sagging cable/wire/rope between two points as a tube mesh (catenary-shaped cosh profile).
    start/end: XYZ tuples or object names (their world location is used). sag = drop at midpoint in m.
    Creates new mesh object `name` at world origin. Exposed inputs: Start, End, Sag, Radius, Resolution.'''
    def pt(v):
        if isinstance(v, str):
            return tuple(get_obj(v).matrix_world.translation)
        if len(v) != 3:
            raise ValueError("start/end must be (x,y,z) or an object name")
        return tuple(float(x) for x in v)
    s, e = pt(start), pt(end)
    obj = _empty_mesh_obj(name)
    k = 1.5
    ck = math.cosh(k)
    inputs = [_I("Start", "VECTOR", s), _I("End", "VECTOR", e), _I("Sag", "FLOAT", sag),
              _I("Radius", "FLOAT", radius, 0.0), _I("Resolution", "INT", max(2, int(resolution)), 2)]
    M = "ShaderNodeMath"
    nodes = [{"name": "Line", "type": "GeometryNodeCurvePrimitiveLine"},
             {"name": "Res", "type": "GeometryNodeResampleCurve", "inputs": {"Mode": "Count"}},
             {"name": "SP", "type": "GeometryNodeSplineParameter"},
             {"name": "M1", "type": M, "props": {"operation": "MULTIPLY_ADD"}, "inputs": {"1": 2.0, "2": -1.0}},
             {"name": "M2", "type": M, "props": {"operation": "MULTIPLY"}, "inputs": {"1": k}},
             {"name": "Ch", "type": M, "props": {"operation": "COSH"}},
             {"name": "M3", "type": M, "props": {"operation": "SUBTRACT"}, "inputs": {"1": ck}},
             {"name": "M4", "type": M, "props": {"operation": "DIVIDE"}, "inputs": {"1": ck - 1.0}},
             {"name": "M5", "type": M, "props": {"operation": "MULTIPLY"}},
             {"name": "Off", "type": "ShaderNodeCombineXYZ"},
             {"name": "SetP", "type": "GeometryNodeSetPosition"},
             {"name": "Prof", "type": "GeometryNodeCurvePrimitiveCircle", "inputs": {"Resolution": 8}},
             {"name": "C2M", "type": "GeometryNodeCurveToMesh", "inputs": {"Fill Caps": True}},
             {"name": "Smooth", "type": "GeometryNodeSetShadeSmooth"}]
    links = [["Group Input", "Start", "Line", "Start"], ["Group Input", "End", "Line", "End"],
             ["Line", "Curve", "Res", "Curve"], ["Group Input", "Resolution", "Res", "Count"],
             ["SP", "Factor", "M1", 0], ["M1", "Value", "M2", 0], ["M2", "Value", "Ch", 0], ["Ch", "Value", "M3", 0],
             ["M3", "Value", "M4", 0], ["M4", "Value", "M5", 0], ["Group Input", "Sag", "M5", 1],
             ["M5", "Value", "Off", "Z"], ["Res", "Curve", "SetP", "Geometry"], ["Off", "Vector", "SetP", "Offset"],
             ["Group Input", "Radius", "Prof", "Radius"], ["SetP", "Geometry", "C2M", "Curve"],
             ["Prof", "Curve", "C2M", "Profile Curve"], ["C2M", "Mesh", "Smooth", "Mesh"],
             ["Smooth", "Mesh", "Group Output", "Geometry"]]
    out = _gn(obj, modifier, inputs, nodes, links)
    out.update({"start": r(s), "end": r(e)})
    return out


@recipe("geonodes", tags=("fur", "hair", "cards", "stylized", "wind", "normals"))
def stylized_fur_cards(target=None, density=200.0, seed=0, card_length=0.12, card_width=0.04,
                       wind_strength=0.25, wind_speed=2.0, keep_surface=True, modifier="BP_FurCards") -> dict:
    '''Stylized fur/grass/feather cards with the "steal the surface normals" trick for soft toon shading.
    Distributes vertical quad cards along normals, wind-sways them (Scene Time sine, per-card phase), stores a random
    per-card color "fur_color" (instance domain -> survives Realize), realizes, then Set Mesh Normal (Free) with normals
    sampled from the body via Sample Nearest Surface. Material M_Fur reads "fur_color". target=None makes a demo sphere.
    Exposed: Density, Seed, Card Length, Card Width, Wind Strength (rad), Wind Speed. Cards carry a UVMap for alpha textures.'''
    obj, demo = _target(target, lambda: _demo_mesh("FurBody", "sphere", size=1.0, segments=(32, 16)))
    mat = _mat("M_Fur", (0.8, 0.5, 0.3, 1))
    nt = mat.node_tree
    if nt and "FurColor" not in nt.nodes:
        a = nt.nodes.new("ShaderNodeAttribute")
        a.name = "FurColor"
        a.attribute_name = "fur_color"
        bsdf = nt.nodes.get("Principled BSDF")
        if bsdf:
            nt.links.new(a.outputs["Color"], bsdf.inputs["Base Color"])
    inputs = [_I("Density", "FLOAT", density, 0.0), _I("Seed", "INT", int(seed)),
              _I("Card Length", "FLOAT", card_length, 0.0), _I("Card Width", "FLOAT", card_width, 0.0),
              _I("Wind Strength", "FLOAT", wind_strength), _I("Wind Speed", "FLOAT", wind_speed),
              _I("Material", "MATERIAL", mat.name)]
    M = "ShaderNodeMath"
    nodes = [{"name": "Dist", "type": "GeometryNodeDistributePointsOnFaces"},
             {"name": "Card", "type": "GeometryNodeMeshGrid", "inputs": {"Vertices X": 2, "Vertices Y": 3}},
             {"name": "UV", "type": "GeometryNodeStoreNamedAttribute",
              "props": {"data_type": "FLOAT2", "domain": "CORNER"}, "inputs": {"Name": "UVMap"}},
             {"name": "HalfL", "type": M, "props": {"operation": "MULTIPLY"}, "inputs": {"1": 0.5}},
             {"name": "Up", "type": "ShaderNodeCombineXYZ"},
             {"name": "CardT", "type": "GeometryNodeTransform", "inputs": {"Rotation": [math.pi / 2, 0, 0]}},
             {"name": "RandS", "type": "FunctionNodeRandomValue", "props": {"data_type": "FLOAT"},
              "inputs": {"Min": 0.7, "Max": 1.3}},
             {"name": "IOP", "type": "GeometryNodeInstanceOnPoints"},
             {"name": "Time", "type": "GeometryNodeInputSceneTime"},
             {"name": "Seed3", "type": M, "props": {"operation": "ADD"}, "inputs": {"1": 3.0}},
             {"name": "Phase", "type": "FunctionNodeRandomValue", "props": {"data_type": "FLOAT"},
              "inputs": {"Min": 0.0, "Max": TAU}},
             {"name": "Wt", "type": M, "props": {"operation": "MULTIPLY"}},
             {"name": "Wa", "type": M, "props": {"operation": "ADD"}},
             {"name": "Ws", "type": M, "props": {"operation": "SINE"}},
             {"name": "Wm", "type": M, "props": {"operation": "MULTIPLY"}},
             {"name": "AA", "type": "FunctionNodeAxisAngleToRotation", "inputs": {"Axis": [1, 0, 0]}},
             {"name": "Wind", "type": "GeometryNodeRotateInstances", "inputs": {"Local Space": True}},
             {"name": "Seed5", "type": M, "props": {"operation": "ADD"}, "inputs": {"1": 5.0}},
             {"name": "RCol", "type": "FunctionNodeRandomValue", "props": {"data_type": "FLOAT_VECTOR"},
              "inputs": {"Min": [0.6, 0.6, 0.6], "Max": [1.0, 1.0, 1.0]}},
             {"name": "Col", "type": "GeometryNodeStoreNamedAttribute",
              "props": {"data_type": "FLOAT_COLOR", "domain": "INSTANCE"}, "inputs": {"Name": "fur_color"}},
             {"name": "Real", "type": "GeometryNodeRealizeInstances"},
             {"name": "Nrm", "type": "GeometryNodeInputNormal"},
             {"name": "Pos", "type": "GeometryNodeInputPosition"},
             {"name": "SNS", "type": "GeometryNodeSampleNearestSurface", "props": {"data_type": "FLOAT_VECTOR"}},
             {"name": "SMN", "type": "GeometryNodeSetMeshNormal", "props": {"mode": "FREE", "domain": "POINT"}},
             {"name": "Mat", "type": "GeometryNodeSetMaterial"},
             {"name": "Join", "type": "GeometryNodeJoinGeometry"}]
    links = [["Group Input", "Geometry", "Dist", "Mesh"], ["Group Input", "Density", "Dist", "Density"],
             ["Group Input", "Seed", "Dist", "Seed"],
             ["Group Input", "Card Width", "Card", "Size X"], ["Group Input", "Card Length", "Card", "Size Y"],
             ["Card", "Mesh", "UV", "Geometry"], ["Card", "UV Map", "UV", "Value"],
             ["Group Input", "Card Length", "HalfL", 0], ["HalfL", "Value", "Up", "Z"],
             ["UV", "Geometry", "CardT", "Geometry"], ["Up", "Vector", "CardT", "Translation"],
             ["Group Input", "Seed", "RandS", "Seed"],
             ["Dist", "Points", "IOP", "Points"], ["CardT", "Geometry", "IOP", "Instance"],
             ["Dist", "Rotation", "IOP", "Rotation"], ["RandS", "Value", "IOP", "Scale"],
             ["Group Input", "Seed", "Seed3", 0], ["Seed3", "Value", "Phase", "Seed"],
             ["Time", "Seconds", "Wt", 0], ["Group Input", "Wind Speed", "Wt", 1],
             ["Wt", "Value", "Wa", 0], ["Phase", "Value", "Wa", 1], ["Wa", "Value", "Ws", 0],
             ["Ws", "Value", "Wm", 0], ["Group Input", "Wind Strength", "Wm", 1], ["Wm", "Value", "AA", "Angle"],
             ["IOP", "Instances", "Wind", "Instances"], ["AA", "Rotation", "Wind", "Rotation"],
             ["Group Input", "Seed", "Seed5", 0], ["Seed5", "Value", "RCol", "Seed"],
             ["Wind", "Instances", "Col", "Geometry"], ["RCol", "Value", "Col", "Value"],
             ["Col", "Geometry", "Real", "Geometry"],
             ["Group Input", "Geometry", "SNS", "Mesh"], ["Nrm", "Normal", "SNS", "Value"],
             ["Pos", "Position", "SNS", "Sample Position"],
             ["Real", "Geometry", "SMN", "Mesh"], ["SNS", "Value", "SMN", "Custom Normal"],
             ["SMN", "Mesh", "Mat", "Geometry"], ["Group Input", "Material", "Mat", "Material"],
             ["Mat", "Geometry", "Join", "Geometry"], ["Join", "Geometry", "Group Output", "Geometry"]]
    if keep_surface:
        links.insert(0, ["Group Input", "Geometry", "Join", "Geometry"])
    out = _gn(obj, modifier, inputs, nodes, links)
    out.update({"material": mat.name, "color_attribute": "fur_color", "demo": demo})
    return out


@recipe("geonodes", tags=("terrain", "landscape", "noise", "ground"))
def terrain_from_noise(name="Terrain", size=20.0, resolution=128, height=3.0, noise_scale=0.15, detail=4.0,
                       seed=0.0, location=(0, 0, 0), modifier="BP_Terrain") -> dict:
    '''Noise-displaced grid terrain (smooth shaded, with UVMap) as a new mesh object `name`.
    resolution = verts per side (128 -> ~32k tris; use 32-64 for Quest). height = peak-to-valley range.
    Exposed inputs: Size, Resolution, Height, Noise Scale, Detail, Seed (4D noise W - change for new shapes).'''
    obj = _empty_mesh_obj(name, location)
    inputs = [_I("Size", "FLOAT", size, 0.01), _I("Resolution", "INT", max(2, int(resolution)), 2),
              _I("Height", "FLOAT", height), _I("Noise Scale", "FLOAT", noise_scale, 0.0),
              _I("Detail", "FLOAT", detail, 0.0, 15.0), _I("Seed", "FLOAT", float(seed))]
    M = "ShaderNodeMath"
    nodes = [{"name": "Grid", "type": "GeometryNodeMeshGrid"},
             {"name": "UV", "type": "GeometryNodeStoreNamedAttribute",
              "props": {"data_type": "FLOAT2", "domain": "CORNER"}, "inputs": {"Name": "UVMap"}},
             {"name": "Noise", "type": "ShaderNodeTexNoise", "props": {"noise_dimensions": "4D"}},
             {"name": "Ctr", "type": M, "props": {"operation": "SUBTRACT"}, "inputs": {"1": 0.5}},
             {"name": "H2", "type": M, "props": {"operation": "MULTIPLY"}, "inputs": {"1": 2.0}},
             {"name": "Amp", "type": M, "props": {"operation": "MULTIPLY"}},
             {"name": "Off", "type": "ShaderNodeCombineXYZ"},
             {"name": "SetP", "type": "GeometryNodeSetPosition"},
             {"name": "Smooth", "type": "GeometryNodeSetShadeSmooth"}]
    links = [["Group Input", "Size", "Grid", "Size X"], ["Group Input", "Size", "Grid", "Size Y"],
             ["Group Input", "Resolution", "Grid", "Vertices X"], ["Group Input", "Resolution", "Grid", "Vertices Y"],
             ["Grid", "Mesh", "UV", "Geometry"], ["Grid", "UV Map", "UV", "Value"],
             ["Group Input", "Noise Scale", "Noise", "Scale"], ["Group Input", "Detail", "Noise", "Detail"],
             ["Group Input", "Seed", "Noise", "W"],
             ["Noise", "Factor", "Ctr", 0], ["Group Input", "Height", "H2", 0], ["Ctr", "Value", "Amp", 0],
             ["H2", "Value", "Amp", 1], ["Amp", "Value", "Off", "Z"],
             ["UV", "Geometry", "SetP", "Geometry"], ["Off", "Vector", "SetP", "Offset"],
             ["SetP", "Geometry", "Smooth", "Mesh"], ["Smooth", "Mesh", "Group Output", "Geometry"]]
    return _gn(obj, modifier, inputs, nodes, links)


@recipe("geonodes", tags=("greebles", "sci-fi", "extrude", "hard-surface", "detail"))
def random_greebles(target=None, probability=0.35, height_min=0.03, height_max=0.2, inset=0.75, seed=0,
                    modifier="BP_Greebles") -> dict:
    '''Random sci-fi greebles: inset + extrude a random subset of faces by random heights (non-destructive).
    Works best on meshes with many evenly-sized quads. target=None makes a demo subdivided cube (5 cuts).
    Exposed inputs: Probability, Height Min, Height Max, Inset (0-1 face scale), Seed. Adds faces: watch the XR tri budget.'''
    obj, demo = _target(target, lambda: _demo_mesh("GreebleBox", "cube", size=2.0, cuts=5))
    inputs = [_I("Probability", "FLOAT", probability, 0.0, 1.0), _I("Height Min", "FLOAT", height_min),
              _I("Height Max", "FLOAT", height_max), _I("Inset", "FLOAT", inset, 0.0, 1.0), _I("Seed", "INT", int(seed))]
    nodes = [{"name": "Sel", "type": "FunctionNodeRandomValue", "props": {"data_type": "BOOLEAN"}},
             {"name": "Ex1", "type": "GeometryNodeExtrudeMesh", "props": {"mode": "FACES"},
              "inputs": {"Offset Scale": 0.0, "Individual": True}},
             {"name": "Sc", "type": "GeometryNodeScaleElements", "props": {"domain": "FACE"}},
             {"name": "Hgt", "type": "FunctionNodeRandomValue", "props": {"data_type": "FLOAT"}},
             {"name": "Ex2", "type": "GeometryNodeExtrudeMesh", "props": {"mode": "FACES"}, "inputs": {"Individual": True}}]
    links = [["Group Input", "Probability", "Sel", "Probability"], ["Group Input", "Seed", "Sel", "Seed"],
             ["Group Input", "Geometry", "Ex1", "Mesh"], ["Sel", "Value", "Ex1", "Selection"],
             ["Ex1", "Mesh", "Sc", "Geometry"], ["Ex1", "Top", "Sc", "Selection"], ["Group Input", "Inset", "Sc", "Scale"],
             ["Group Input", "Height Min", "Hgt", "Min"], ["Group Input", "Height Max", "Hgt", "Max"],
             ["Group Input", "Seed", "Hgt", "Seed"],
             ["Sc", "Geometry", "Ex2", "Mesh"], ["Ex1", "Top", "Ex2", "Selection"], ["Hgt", "Value", "Ex2", "Offset Scale"],
             ["Ex2", "Mesh", "Group Output", "Geometry"]]
    before = len(obj.data.polygons)
    out = _gn(obj, modifier, inputs, nodes, links)
    out.update({"faces_before": before, "demo": demo})
    return out


_REALIZE_GROUP = ".BP_RealizeAll"


def _realize_group():
    ng = bpy.data.node_groups.get(_REALIZE_GROUP)
    if ng is None:
        ng = bpy.data.node_groups.new(_REALIZE_GROUP, "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
        rn = ng.nodes.new("GeometryNodeRealizeInstances")
        ng.links.new(gi.outputs[0], rn.inputs[0])
        ng.links.new(rn.outputs[0], go.inputs[0])
    return ng


@recipe("geonodes", tags=("bake", "apply", "realize", "export"))
def realize_and_apply_gn(target=None, keep_original=True, name=None) -> dict:
    '''Bake an object's full modifier/Geometry Nodes result (instances realized) into a real, exportable mesh.
    keep_original=True creates `<name>_baked` and hides the procedural original (non-destructive);
    False replaces the original's mesh and strips its modifiers. GN-assigned materials and attributes are kept.
    target=None runs scatter_on_surface on a demo plane first and bakes that.'''
    if target is None:
        target = scatter_on_surface()["object"]
    obj = get_obj(target)
    if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "POINTCLOUD", "CURVES"}:
        raise ValueError(f"{obj.name} ({obj.type}) cannot be baked to a mesh")
    tmp = obj.modifiers.new("BP_TmpRealize", "NODES")
    tmp.node_group = _realize_group()
    try:
        me = _mesh_from_eval(obj)
    finally:
        obj.modifiers.remove(tmp)
    if len(me.vertices) == 0:
        bpy.data.meshes.remove(me)
        raise ValueError(f"{obj.name} evaluates to no mesh geometry (points/curves only?)")
    if keep_original or obj.type != "MESH":
        new = bpy.data.objects.new(name or f"{obj.name}_baked", me)
        new.matrix_world = obj.matrix_world.copy()
        for c in obj.users_collection:
            c.objects.link(new)
        obj.hide_set(True) if obj.name in bpy.context.view_layer.objects else None
        obj.hide_render = True
        out_obj = new
    else:
        old = obj.data
        obj.modifiers.clear()
        obj.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)
        out_obj = obj
    me.name = out_obj.name
    return {"object": out_obj.name, "source": obj.name, "verts": len(me.vertices), "faces": len(me.polygons),
            "tris": _tris(me), "materials": [m.name for m in me.materials if m]}


# ============================================================== PIPELINE

@recipe("pipeline", tags=("lod", "decimate", "unity", "optimization", "quest"))
def generate_lods(target=None, ratios=(1.0, 0.5, 0.25, 0.1), symmetry=False, hide_lower=True) -> dict:
    '''Create LOD0..N copies (Decimate collapse, modifiers applied) named <base>_LOD0.. under an empty <base>_LODGroup.
    Unity auto-builds a LODGroup from FBX children with _LOD0/_LOD1 suffixes. Original is untouched.
    ratios: decimate ratio per level (first is usually 1.0). target=None makes a demo 64x32 UV sphere.'''
    obj, demo = _target(target, lambda: _demo_mesh("LodRock", "sphere", size=1.0, segments=(64, 32)))
    if not ratios or any(not (0 < float(x) <= 1) for x in ratios):
        raise ValueError("ratios must be a non-empty list of floats in (0, 1]")
    base = _base_name(obj.name)
    grp = bpy.data.objects.new(f"{base}_LODGroup", None)
    grp.matrix_world = obj.matrix_world.copy()
    coll = obj.users_collection[0] if obj.users_collection else _coll()
    coll.objects.link(grp)
    src = _mesh_from_eval(obj)
    lods = []
    try:
        for i, ratio in enumerate(ratios):
            me = src.copy()
            lod = bpy.data.objects.new(f"{base}_LOD{i}", me)
            coll.objects.link(lod)
            if ratio < 1.0:
                d = lod.modifiers.new("Decimate", "DECIMATE")
                d.ratio = float(ratio)
                d.use_symmetry = bool(symmetry)
                baked = _mesh_from_eval(lod)
                lod.modifiers.clear()
                lod.data = baked
                bpy.data.meshes.remove(me)
                me = baked
            me.name = lod.name
            lod.parent = grp
            lod.matrix_parent_inverse.identity()
            lod.matrix_basis.identity()
            if hide_lower and i > 0:
                lod.hide_set(True)
            lods.append({"name": lod.name, "ratio": ratio, "tris": _tris(me)})
    finally:
        bpy.data.meshes.remove(src)
    return {"group": grp.name, "source": obj.name, "lods": lods, "demo": demo}


@recipe("pipeline", tags=("collision", "convex", "unity", "physics", "ucx"))
def unity_collision_mesh(target=None, max_tris=255, name=None) -> dict:
    '''Build a convex-hull collision mesh UCX_<base>_00 parented to the target (same transform), wireframe, no render.
    Kept under max_tris (Unity convex MeshCollider limit 255) by decimating + re-hulling. UCX_ is the Unreal/FBX
    convention; in Unity assign it to a convex MeshCollider (or let an import script do it). target=None: demo sphere.'''
    obj, demo = _target(target, lambda: _demo_mesh("PropBody", "sphere", size=1.0, segments=(48, 24)))
    me_src = _mesh_from_eval(obj)
    pts = [v.co.copy() for v in me_src.vertices]
    bpy.data.meshes.remove(me_src)
    if len(pts) < 4:
        raise ValueError(f"{obj.name} needs at least 4 vertices for a hull")

    def hull(points):
        bm = bmesh.new()
        for p in points:
            bm.verts.new(p)
        res = bmesh.ops.convex_hull(bm, input=bm.verts[:])
        bmesh.ops.delete(bm, geom=res["geom_interior"] + res["geom_unused"], context="VERTS")
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        return bm
    bm = hull(pts)
    cname = name or f"UCX_{_base_name(obj.name)}_00"
    me = bpy.data.meshes.new(cname)
    bm.to_mesh(me)
    bm.free()
    col = bpy.data.objects.new(cname, me)
    for c in obj.users_collection or [_coll()]:
        c.objects.link(col)
    ratio, passes = 1.0, 0
    while _tris(col.data) > max_tris and passes < 12:
        ratio = max_tris / _tris(col.data) * 0.95
        d = col.modifiers.new("Dec", "DECIMATE")
        d.ratio = ratio
        dec = _mesh_from_eval(col)
        col.modifiers.clear()
        bm = hull([v.co.copy() for v in dec.vertices])
        bpy.data.meshes.remove(dec)
        bm.to_mesh(col.data)
        bm.free()
        passes += 1
    col.parent = obj
    col.matrix_parent_inverse.identity()
    col.matrix_basis.identity()
    col.display_type = "WIRE"
    col.hide_render = True
    return {"collision": col.name, "parent": obj.name, "tris": _tris(col.data), "verts": len(col.data.vertices),
            "decimate_passes": passes, "demo": demo}


@recipe("pipeline", tags=("uv", "lightmap", "uv2", "unity", "baking"))
def lightmap_uv2(targets=None, uv_name="UV2", margin=0.05, force=False) -> dict:
    '''Ensure each mesh has a second, non-overlapping UV channel for lightmaps (Unity UV1 / "UV2").
    Skips meshes that already have >=2 UV layers unless force=True (then repacks the 2nd layer). Creates UVMap first if
    the mesh has none. Uses Lightmap Pack per mesh; UV0 stays active/render. targets=None: all meshes (demo cube if none).'''
    objs = _meshes(targets, allow_demo=lambda: [_demo_mesh("LightmapBox", "cube", size=1.0)])
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    out, seen = {}, set()
    for o in objs:
        me = o.data
        if me.name in seen:
            out[o.name] = "shared mesh, done"
            continue
        seen.add(me.name)
        if len(me.uv_layers) == 0:
            me.uv_layers.new(name="UVMap")
        if len(me.uv_layers) >= 2 and not force:
            out[o.name] = {"status": "exists", "layers": [l.name for l in me.uv_layers]}
            continue
        lay = me.uv_layers[1] if len(me.uv_layers) >= 2 else me.uv_layers.new(name=uv_name)
        if lay is None:
            raise RuntimeError(f"could not add UV layer to {o.name} (max 8)")
        lay.name = uv_name if uv_name not in me.uv_layers or me.uv_layers[uv_name] == lay else lay.name
        me.uv_layers.active = lay
        ctx = dict(find_view3d())
        ctx.update(active_object=o, object=o, selected_objects=[o], selected_editable_objects=[o])
        with bpy.context.temp_override(**ctx):
            bpy.ops.uv.lightmap_pack(PREF_CONTEXT="ALL_FACES", PREF_PACK_IN_ONE=True, PREF_NEW_UVLAYER=False,
                                     PREF_MARGIN_DIV=margin)
        me.uv_layers.active = me.uv_layers[0]
        me.uv_layers[0].active_render = True
        us = [c for d in lay.uv for c in d.vector]
        out[o.name] = {"status": "packed", "layers": [l.name for l in me.uv_layers],
                       "uv2_range": r([min(us), max(us)]) if us else None}
    return {"objects": out}


@recipe("pipeline", tags=("apply", "freeze", "transforms", "modifiers", "export"))
def apply_and_freeze_for_export(targets=None, suffix="_EXPORT", collection="Export", keep_location=False) -> dict:
    '''Make export-ready copies: modifiers applied, rotation/scale (and location unless keep_location) baked into the mesh,
    unparented, placed in collection `collection`. Originals untouched. Negative scale is fixed (normals flipped back).
    targets=None: all meshes (demo rotated/scaled cube with Bevel if scene empty).'''
    def demo():
        o = _demo_mesh("FreezeBox", "cube", size=1.0, location=(1, 2, 0.5))
        o.rotation_euler = (0, 0, math.radians(30))
        o.scale = (2, 1, 1)
        o.modifiers.new("Bevel", "BEVEL").width = 0.05
        return [o]
    objs = _meshes(targets, allow_demo=demo)
    coll = bpy.data.collections.get(collection)
    if coll is None:
        coll = bpy.data.collections.new(collection)
        bpy.context.scene.collection.children.link(coll)
    objs = [o for o in objs if coll not in o.users_collection]
    out = []
    for o in objs:
        me = _mesh_from_eval(o)
        mw = o.matrix_world.copy()
        if keep_location:
            loc = mw.to_translation()
            bake = mathutils.Matrix.Translation(-loc) @ mw
        else:
            loc, bake = mathutils.Vector(), mw
        me.transform(bake)
        if bake.to_3x3().determinant() < 0:
            bm = bmesh.new()
            bm.from_mesh(me)
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
            bm.to_mesh(me)
            bm.free()
        cp = bpy.data.objects.new(o.name + suffix, me)
        me.name = cp.name
        cp.location = loc
        coll.objects.link(cp)
        out.append({"source": o.name, "copy": cp.name, "applied_modifiers": len(o.modifiers),
                    "verts": len(me.vertices), "location": r(list(cp.location))})
    return {"collection": coll.name, "copies": out}


_EXT = {"unity_fbx": ".fbx", "glb": ".glb", "webxr_glb": ".glb", "obj": ".obj", "usd": ".usdc"}


@recipe("pipeline", tags=("export", "batch", "fbx", "glb", "unity"))
def batch_export(objects=None, collections=None, by="object", preset="unity_fbx", directory=None, center=True,
                 include_children=True, options=None) -> dict:
    '''Export each object (by="object") or each collection (by="collection") to its own file with an export preset
    (unity_fbx | glb | webxr_glb | obj | usd). center=True temporarily moves each root to the world origin (prefab-ready).
    include_children exports LOD/UCX children with their parent. directory defaults to <temp>/blendpro_export.
    objects=None: top-level visible meshes/empties (2 demo cubes if the scene is empty); collections=None: scene children.'''
    from .. import ops
    if preset not in _EXT:
        raise ValueError(f"preset must be one of {list(_EXT)}")
    directory = os.path.abspath(directory or _EXPORT_DIR)
    os.makedirs(directory, exist_ok=True)
    jobs = []
    if by == "object":
        if objects:
            roots = [get_obj(n) for n in ([objects] if isinstance(objects, str) else objects)]
        else:
            roots = [o for o in bpy.context.scene.objects if o.parent is None and o.type in {"MESH", "EMPTY", "ARMATURE"}
                     and o.visible_get()]
            if not roots:
                roots = [_demo_mesh("BatchCrate", "cube", 1.0, (2, 0, 0)), _demo_mesh("BatchBall", "sphere", 1.0, (-2, 0, 0))]
        for o in roots:
            objs = [o] + (list(o.children_recursive) if include_children else [])
            jobs.append((o.name, [x for x in objs if x.type in {"MESH", "EMPTY", "ARMATURE"}], [o] if center else []))
    elif by == "collection":
        colls = [bpy.data.collections[c] for c in collections] if collections else list(bpy.context.scene.collection.children)
        for c in colls:
            objs = [o for o in c.all_objects if o.type in {"MESH", "EMPTY", "ARMATURE"}]
            if objs:
                jobs.append((c.name, objs, [o for o in objs if o.parent is None] if center else []))
        if not jobs:
            raise ValueError("no collections with exportable objects")
    else:
        raise ValueError("by must be 'object' or 'collection'")
    files = []
    for fname, objs, movers in jobs:
        saved = {m.name: m.matrix_world.copy() for m in movers}
        if movers and by == "collection":  # keep relative layout: shift all roots by the first root's offset
            off = movers[0].matrix_world.translation.copy()
            for m in movers:
                m.matrix_world.translation = m.matrix_world.translation - off
        else:
            for m in movers:
                m.matrix_world.translation = (0, 0, 0)
        bpy.context.view_layer.update()
        try:
            path = os.path.join(directory, bpy.path.clean_name(fname) + _EXT[preset])
            rep = ops.export_asset(path, objects=[o.name for o in objs], preset=preset, options=options, check=False)
        finally:
            for m in movers:
                m.matrix_world = saved[m.name]
        files.append({"name": fname, "path": rep["filepath"], "size_kb": rep["size_kb"], "objects": len(objs)})
    return {"directory": directory, "preset": preset, "files": files}


def _strip_suffix(n):
    import re
    return re.sub(r"\.\d{3,}$", "", n)


@recipe("pipeline", tags=("naming", "convention", "cleanup", "unity", "unreal"))
def naming_convention_fix(dry_run=False, strip_numeric_suffix=True, rename_mesh_data=True, prefixes=None,
                          demo=False) -> dict:
    '''Apply game-engine naming: SM_ static meshes, SK_ skinned meshes (armature modifier), SKEL_ armatures,
    M_ materials, T_ images; strips Blender ".001" suffixes when the clean name is free (else _01, _02...), and renames
    mesh data to match objects. dry_run=True only reports. prefixes overrides e.g. {"material": "MAT_"}.
    demo=True first creates badly named sample data. Returns {kind: {old: new}} (capped at 40 per kind).'''
    pre = {"static": "SM_", "skinned": "SK_", "armature": "SKEL_", "material": "M_", "image": "T_"}
    pre.update(prefixes or {})
    if demo:
        a = _demo_mesh("Cube.001", "cube")
        _demo_mesh("crate", "cube", location=(2, 0, 0))
        a.data.materials.append(bpy.data.materials.new("Material.002"))
        bpy.data.images.new("albedo.001", 8, 8)
    all_pre = tuple(pre.values())
    renames = {"objects": {}, "materials": {}, "images": {}, "meshes": {}}

    def clean(old, prefix, pool):
        n = _strip_suffix(old) if strip_numeric_suffix else old
        for p in all_pre:
            if n.startswith(p):
                n = n[len(p):]
                break
        n = prefix + n
        if n == old:
            return old
        cand, i = n, 1
        while cand in pool and pool[cand] is not None and cand != old:
            cand = f"{n}_{i:02d}"
            i += 1
        return cand

    for o in list(bpy.data.objects):
        if o.type == "MESH":
            skinned = any(m.type == "ARMATURE" for m in o.modifiers) or (o.parent and o.parent.type == "ARMATURE")
            prefix = pre["skinned"] if skinned else pre["static"]
        elif o.type == "ARMATURE":
            prefix = pre["armature"]
        else:
            continue
        if o.name.startswith("UCX_") or "_LOD" in o.name and o.name.startswith(prefix):
            continue
        new = clean(o.name, prefix, bpy.data.objects)
        if new != o.name:
            renames["objects"][o.name] = new
            if not dry_run:
                o.name = new
        if rename_mesh_data and o.type == "MESH" and o.data.users == 1 and o.data.name != (new if not dry_run else o.name):
            target = new if dry_run else o.name
            renames["meshes"][o.data.name] = target
            if not dry_run:
                o.data.name = target
    for kind, coll, prefix in (("materials", bpy.data.materials, pre["material"]), ("images", bpy.data.images, pre["image"])):
        for idb in list(coll):
            if idb.library or idb.name in {"Render Result", "Viewer Node", "Dots Stroke"}:
                continue
            new = clean(idb.name, prefix, coll)
            if new != idb.name:
                renames[kind][idb.name] = new
                if not dry_run:
                    idb.name = new
    counts = {k: len(v) for k, v in renames.items()}
    return {"dry_run": dry_run, "counts": counts, "renames": {k: dict(list(v.items())[:40]) for k, v in renames.items() if v}}


@recipe("pipeline", tags=("cleanup", "orphans", "purge", "file-size"))
def purge_orphans(recursive=True, demo=False) -> dict:
    '''Delete all datablocks with zero users (meshes, materials, images, node groups...), recursively by default.
    IDs with a fake user (shield icon) are kept. Shrinks .blend size and avoids stray assets in exports.
    demo=True first creates an orphan mesh + material + image so the effect is visible.'''
    if demo:
        bpy.data.meshes.new("OrphanMesh")
        bpy.data.materials.new("OrphanMat")
        bpy.data.images.new("OrphanImg", 4, 4)
    kinds = ("meshes", "materials", "images", "node_groups", "textures", "curves", "actions", "collections",
             "armatures", "lights", "cameras", "worlds", "objects", "brushes")
    before = {k: len(getattr(bpy.data, k)) for k in kinds}
    removed = bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=bool(recursive))
    delta = {k: before[k] - len(getattr(bpy.data, k)) for k in kinds if before[k] != len(getattr(bpy.data, k))}
    return {"removed": removed, "by_type": delta}


_PATH_KINDS = ("images", "libraries", "fonts", "sounds", "movieclips", "volumes", "cache_files")


@recipe("pipeline", tags=("textures", "pack", "missing-files", "paths", "relative"))
def texture_files_maintenance(action="report", search_dir=None, unpack_method="WRITE_LOCAL", demo=False) -> dict:
    '''External-file housekeeping. action: report (missing/packed/external files for images, libraries, fonts, sounds,
    clips, volumes, caches) | pack (pack all existing image files into the .blend) | unpack (unpack_method e.g.
    WRITE_LOCAL / USE_ORIGINAL) | relative / absolute (rewrite paths; needs a saved .blend) | find_missing (search
    search_dir recursively by file name and relink). demo=True adds one real temp PNG + one missing image first.'''
    if demo:
        d = os.path.join(tempfile.gettempdir(), "blendpro_tex")
        os.makedirs(d, exist_ok=True)
        img = bpy.data.images.new("demo_albedo", 64, 64)
        img.filepath_raw = os.path.join(d, "demo_albedo.png")
        img.file_format = "PNG"
        img.save()
        img.source = "FILE"
        miss = bpy.data.images.new("demo_missing", 8, 8)
        miss.source = "FILE"
        miss.filepath = os.path.join(d, "does_not_exist_9f3a.png")

    def entries():
        for kind in _PATH_KINDS:
            for idb in getattr(bpy.data, kind, []):
                fp = getattr(idb, "filepath", "")
                if kind == "images" and idb.source not in {"FILE", "SEQUENCE", "TILED", "MOVIE"}:
                    continue
                if not fp or fp == "<builtin>":
                    continue
                yield kind, idb, fp

    changed = []
    if action == "pack":
        for kind, idb, fp in entries():
            if kind == "images" and not idb.packed_file and os.path.exists(bpy.path.abspath(fp)):
                idb.pack()
                changed.append(idb.name)
    elif action == "unpack":
        for kind, idb, fp in entries():
            if getattr(idb, "packed_file", None):
                idb.unpack(method=unpack_method)
                changed.append(idb.name)
    elif action in ("relative", "absolute"):
        if not bpy.data.filepath:
            raise ValueError("save the .blend first: relative/absolute paths are resolved against the .blend location")
        for kind, idb, fp in entries():
            new = bpy.path.relpath(bpy.path.abspath(fp)) if action == "relative" else bpy.path.abspath(fp)
            if action == "relative" and not new.startswith("//"):
                continue  # different drive
            if new != fp:
                idb.filepath = new
                changed.append(idb.name)
    elif action == "find_missing":
        if not search_dir or not os.path.isdir(search_dir):
            raise ValueError("find_missing needs an existing search_dir")
        index = {}
        for root, _, fs in os.walk(search_dir):
            for f in fs:
                index.setdefault(f.lower(), os.path.join(root, f))
        for kind, idb, fp in entries():
            if not getattr(idb, "packed_file", None) and not os.path.exists(bpy.path.abspath(fp)):
                hit = index.get(os.path.basename(bpy.path.abspath(fp)).lower())
                if hit:
                    idb.filepath = hit
                    if kind == "images":
                        idb.reload()
                    changed.append(idb.name)
    elif action != "report":
        raise ValueError("action must be report|pack|unpack|relative|absolute|find_missing")
    missing, packed, external = [], [], []
    for kind, idb, fp in entries():
        if getattr(idb, "packed_file", None):
            packed.append(idb.name)
        elif not os.path.exists(bpy.path.abspath(fp)):
            missing.append({"type": kind, "name": idb.name, "path": fp})
        else:
            external.append(idb.name)
    return {"action": action, "changed": changed, "missing": missing[:50], "missing_count": len(missing),
            "packed": packed[:50], "external": external[:50], "blend_saved": bool(bpy.data.filepath)}


@recipe("pipeline", tags=("units", "scale", "unity", "setup", "metric"))
def scene_units_setup(system="METRIC", scale_length=1.0, length_unit="METERS", fps=None) -> dict:
    '''Set scene units for engine export (Unity/WebXR/visionOS: metric, unit scale 1.0 => 1 BU = 1 m) and report
    objects with unapplied rotation/scale that will import oddly (use apply_and_freeze_for_export).
    fps optionally sets the scene frame rate (e.g. 30/60 for baked animation; headset refresh is separate).'''
    us = bpy.context.scene.unit_settings
    us.system = system
    us.scale_length = float(scale_length)
    if system != "NONE":
        try:
            us.length_unit = length_unit
        except TypeError as e:
            raise ValueError(f"invalid length_unit for {system}: {e}")
        if system == "METRIC":
            us.mass_unit = "KILOGRAMS"
    if fps:
        bpy.context.scene.render.fps = int(fps)
        bpy.context.scene.render.fps_base = 1.0
    bad_scale = [o.name for o in bpy.context.scene.objects if any(abs(s - 1) > 1e-4 for s in o.scale)]
    bad_rot = [o.name for o in bpy.context.scene.objects if o.type == "MESH" and any(abs(a) > 1e-4 for a in o.rotation_euler)]
    return {"system": us.system, "scale_length": r(us.scale_length, 4), "length_unit": us.length_unit,
            "fps": bpy.context.scene.render.fps, "unapplied_scale": bad_scale[:30], "unapplied_rotation": bad_rot[:30]}


_BUDGETS = {  # rules of thumb per rendered frame/scene; override with budgets={...}
    "quest": {"tris": 750_000, "draw_calls": 150, "texture_mb": 512},
    "visionpro": {"tris": 1_000_000, "draw_calls": 300, "texture_mb": 1024},
    "webxr": {"tris": 500_000, "draw_calls": 100, "texture_mb": 256},
}


@recipe("pipeline", tags=("budget", "performance", "quest", "visionpro", "webxr", "report"))
def xr_budget_report(platform="quest", objects=None, budgets=None, top=10, demo=False) -> dict:
    '''Scene performance report vs an XR budget: evaluated tris (instances included), draw calls (~material slots per
    rendered mesh instance), texture memory (RGBA8+mips and ASTC 6x6 estimates). platform: quest|visionpro|webxr
    (rule-of-thumb budgets; pass budgets={"tris":..,"draw_calls":..,"texture_mb":..} to override).
    Lists top offenders. demo=True adds a dense textured sphere first.'''
    if platform not in _BUDGETS:
        raise ValueError(f"platform must be one of {list(_BUDGETS)}")
    bud = dict(_BUDGETS[platform])
    bud.update(budgets or {})
    if demo:
        o = _demo_mesh("BudgetSphere", "sphere", 1.0, segments=(256, 128))
        m = _mat("M_BudgetDemo")
        img = bpy.data.images.new("T_BudgetDemo", 2048, 2048)
        tex = m.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        m.node_tree.links.new(tex.outputs["Color"], m.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
        o.data.materials.append(m)
    only = {get_obj(n).name for n in objects} if objects else None
    dg = bpy.context.evaluated_depsgraph_get()
    cache, per = {}, {}
    images = set()
    for inst in dg.object_instances:
        ob = inst.object
        if ob.type not in {"MESH", "CURVE", "FONT", "SURFACE", "META"}:
            continue
        root = inst.parent.original if inst.is_instance and inst.parent else ob.original
        while root.parent:
            root = root.parent
        if only and root.name not in only:
            continue
        key = ob.data.session_uid if ob.data else id(ob)
        if key not in cache:
            try:
                me = ob.to_mesh()
            except RuntimeError:
                continue
            mats = [m for m in me.materials if m] if me else []
            used = len({p.material_index for p in me.polygons}) if me and len(me.polygons) else 0
            cache[key] = (_tris(me) if me else 0, max(1, min(used, len(me.materials))) if used else 0, mats)
            ob.to_mesh_clear()
        tris, dc, mats = cache[key]
        for m in mats:
            if m.node_tree:
                for n in m.node_tree.nodes:
                    if n.bl_idname == "ShaderNodeTexImage" and n.image:
                        images.add(n.image)
        e = per.setdefault(root.name, {"tris": 0, "draw_calls": 0, "instances": 0})
        e["tris"] += tris
        e["draw_calls"] += dc
        e["instances"] += 1
    tex = []
    rgba = astc = 0.0
    for img in images:
        w, h = img.size
        a = w * h * 4 * 1.333 / 2 ** 20
        b = w * h * (128 / 36 / 8) * 1.333 / 2 ** 20
        rgba += a
        astc += b
        tex.append({"name": img.name, "size": [w, h], "rgba8_mb": round(a, 2)})
    tot = {"tris": sum(e["tris"] for e in per.values()), "draw_calls": sum(e["draw_calls"] for e in per.values()),
           "texture_mb": round(astc, 1)}
    status = {k: {"value": tot[k], "budget": bud[k], "pct": round(100 * tot[k] / bud[k], 1) if bud[k] else None,
                  "ok": tot[k] <= bud[k]} for k in ("tris", "draw_calls", "texture_mb")}
    worst = sorted(per.items(), key=lambda kv: -kv[1]["tris"])[:top]
    tips = []
    if not status["tris"]["ok"]:
        tips.append("generate_lods / Decimate the top offenders")
    if not status["draw_calls"]["ok"]:
        tips.append("merge_by_material or a texture atlas to cut draw calls")
    if not status["texture_mb"]["ok"]:
        tips.append("texture_resize to 1024/2048 max")
    return {"platform": platform, "totals": tot, "texture_rgba8_mb": round(rgba, 1), "status": status,
            "over_budget": [k for k, v in status.items() if not v["ok"]],
            "top_objects": [dict(name=k, **v) for k, v in worst],
            "textures": sorted(tex, key=lambda t: -t["rgba8_mb"])[:top], "tips": tips}


@recipe("pipeline", tags=("textures", "resize", "optimization", "quest", "webxr"))
def texture_resize(max_size=1024, images=None, output_dir=None, replace=True, file_format="PNG", demo=False) -> dict:
    '''Downscale images larger than max_size px (longest side, aspect kept) into new files and optionally swap them in
    for every user (replace=True; originals stay in the .blend/disk). output_dir defaults to <temp>/blendpro_textures.
    images=None: all images with pixels. demo=True adds a 2048 px generated image first. file_format PNG|JPEG|WEBP.'''
    if demo:
        bpy.data.images.new("T_DemoBig", 2048, 1024)
    out_dir = os.path.abspath(output_dir or os.path.join(tempfile.gettempdir(), "blendpro_textures"))
    os.makedirs(out_dir, exist_ok=True)
    ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "TARGA": ".tga"}.get(file_format)
    if ext is None:
        raise ValueError("file_format must be PNG|JPEG|WEBP|TARGA")
    imgs = [bpy.data.images[n] for n in images] if images else [
        i for i in bpy.data.images if i.source in {"FILE", "GENERATED"} and i.name not in {"Render Result", "Viewer Node"}]
    done, skipped = [], []
    for img in imgs:
        w, h = img.size
        if w == 0 or h == 0:
            skipped.append({"name": img.name, "reason": "no pixel data (missing file?)"})
            continue
        if max(w, h) <= max_size:
            skipped.append({"name": img.name, "reason": f"{w}x{h} within limit"})
            continue
        f = max_size / max(w, h)
        nw, nh = max(1, round(w * f)), max(1, round(h * f))
        new = img.copy()
        new.name = f"{_strip_suffix(img.name)}_{max_size}"
        new.scale(nw, nh)
        path = os.path.join(out_dir, bpy.path.clean_name(new.name) + ext)
        new.filepath_raw = path
        new.file_format = file_format
        new.save()
        new.source = "FILE"
        if replace:
            img.user_remap(new)
        done.append({"name": img.name, "new": new.name, "from": [w, h], "to": [nw, nh], "path": path,
                     "size_kb": round(os.path.getsize(path) / 1024, 1)})
    return {"max_size": max_size, "resized": done, "skipped": skipped[:30]}


@recipe("pipeline", tags=("draw-calls", "merge", "join", "optimization", "quest"))
def merge_by_material(objects=None, prefix="SM_Merged", keep_originals=True) -> dict:
    '''Cut draw calls: join meshes that share the same material set into one mesh per set (world transforms and
    modifiers baked, object at world origin). Originals are hidden (keep_originals=True) or deleted.
    objects=None: all visible meshes (demo: 6 cubes with 2 materials if the scene is empty). Don't merge things that
    move independently or need separate culling/LOD.'''
    def demo():
        a, b = _mat("M_Red", (0.8, 0.1, 0.1, 1)), _mat("M_Blue", (0.1, 0.2, 0.8, 1))
        out = []
        for i in range(6):
            o = _demo_mesh(f"Block{i}", "cube", 0.5, (i * 0.8, 0, 0))
            o.data.materials.append(a if i % 2 else b)
            out.append(o)
        return out
    objs = _meshes(objects, allow_demo=demo)
    objs = [o for o in objs if o.visible_get() or objects]
    groups = {}
    for o in objs:
        key = tuple(s.material.name if s.material else "" for s in o.material_slots) or ("",)
        groups.setdefault(key, []).append(o)
    before = sum(max(1, len(o.material_slots)) for o in objs)
    merged = []
    for key, members in groups.items():
        bm = bmesh.new()
        for o in members:
            me = _mesh_from_eval(o)
            me.transform(o.matrix_world)
            bm.from_mesh(me)
            bpy.data.meshes.remove(me)
        label = "_".join(k.replace("M_", "") for k in key if k) or "NoMat"
        me = bpy.data.meshes.new(f"{prefix}_{label}")
        bm.to_mesh(me)
        bm.free()
        for k in key:
            me.materials.append(bpy.data.materials.get(k) if k else None)
        new = bpy.data.objects.new(me.name, me)
        (members[0].users_collection[0] if members[0].users_collection else _coll()).objects.link(new)
        merged.append({"object": new.name, "materials": [k for k in key if k], "sources": len(members), "tris": _tris(me)})
        for o in members:
            if keep_originals:
                o.hide_set(True)
                o.hide_render = True
            else:
                bpy.data.objects.remove(o)
    after = sum(max(1, len([k for k in m["materials"]])) for m in merged)
    return {"merged": merged, "draw_calls_before": before, "draw_calls_after": after}


@recipe("pipeline", tags=("pivot", "origin", "unity", "placement"))
def set_pivot_bottom_center(targets=None, mode="bottom_center", move_to_origin=False) -> dict:
    '''Move each mesh's origin to its bottom-center (or "center") without moving the geometry in the world, so props
    sit on the floor when placed in Unity/visionOS. move_to_origin=True then puts the object at world (0,0,0).
    Children keep their world positions. Shared mesh data is refused (make single-user first). targets=None: all meshes
    (demo offset cube if empty).'''
    if mode not in ("bottom_center", "center"):
        raise ValueError("mode must be bottom_center or center")
    objs = _meshes(targets, allow_demo=lambda: [_demo_mesh("PivotCrate", "cube", 1.0, (2, 3, 1.5))])
    out = []
    for o in objs:
        me = o.data
        if me.users > 1:
            raise ValueError(f"{o.name}: mesh {me.name} is shared by {me.users} users; make it single-user first")
        if not me.vertices:
            continue
        xs = [v.co for v in me.vertices]
        mn = mathutils.Vector((min(c.x for c in xs), min(c.y for c in xs), min(c.z for c in xs)))
        mx = mathutils.Vector((max(c.x for c in xs), max(c.y for c in xs), max(c.z for c in xs)))
        off = (mn + mx) / 2
        if mode == "bottom_center":
            off.z = mn.z
        me.transform(mathutils.Matrix.Translation(-off))
        mw = o.matrix_world.copy()
        o.matrix_world = mw @ mathutils.Matrix.Translation(off)
        for ch in o.children:
            ch.matrix_parent_inverse = mathutils.Matrix.Translation(-off) @ ch.matrix_parent_inverse
        if move_to_origin:
            o.matrix_world.translation = (0, 0, 0)
        bpy.context.view_layer.update()
        bb = world_bbox(o)
        out.append({"object": o.name, "pivot_world": r(list(o.matrix_world.translation)),
                    "local_offset": r(list(off)), "world_min_z": r(bb[0].z) if bb else None})
    return {"objects": out}


@recipe("pipeline", tags=("gltf", "glb", "webxr", "draco", "meshopt", "webp", "export"))
def gltf_optimize_export(filepath=None, objects=None, compression="draco", draco_level=6, image_format="WEBP",
                         image_quality=80, compare=True) -> dict:
    '''Export an optimized GLB for WebXR / three.js / model-viewer: mesh compression (draco | meshopt | none) and
    texture re-encoding (WEBP | JPEG | AUTO | NONE). compare=True also writes an uncompressed <name>_raw.glb and reports
    the saving. Draco/meshopt need decoder support in the viewer (three.js DRACOLoader / MeshoptDecoder).
    filepath defaults to <temp>/blendpro_export/scene_opt.glb; objects=None exports the scene (demo sphere if empty).'''
    from .. import ops
    if compression not in ("draco", "meshopt", "none"):
        raise ValueError("compression must be draco|meshopt|none")
    if image_format not in ("WEBP", "JPEG", "AUTO", "NONE"):
        raise ValueError("image_format must be WEBP|JPEG|AUTO|NONE")
    if not any(o.type == "MESH" for o in bpy.context.scene.objects) and not objects:
        o = _demo_mesh("GltfDemo", "sphere", 1.0, segments=(96, 48))
        o.data.materials.append(_mat("M_GltfDemo", (0.2, 0.6, 0.9, 1)))
    filepath = os.path.abspath(filepath or os.path.join(_EXPORT_DIR, "scene_opt.glb"))
    names = [get_obj(n).name for n in objects] if objects else None
    opts = {"export_image_format": image_format, "export_image_quality": int(image_quality),
            "export_draco_mesh_compression_enable": compression == "draco",
            "export_meshopt_compression_enable": compression == "meshopt"}
    if compression == "draco":
        opts["export_draco_mesh_compression_level"] = int(draco_level)
    try:
        rep = ops.export_asset(filepath, objects=names, preset="glb", options=opts, check=False)
    except Exception as e:
        raise RuntimeError(f"glTF export failed ({compression}): {e}. Try compression='none' or 'meshopt'.")
    out = {"filepath": rep["filepath"], "size_kb": rep["size_kb"], "compression": compression, "image_format": image_format}
    if compare:
        raw = os.path.splitext(filepath)[0] + "_raw.glb"
        rr = ops.export_asset(raw, objects=names, preset="glb", options={"export_image_format": "AUTO"}, check=False)
        out["raw_size_kb"] = rr["size_kb"]
        out["saving_pct"] = round(100 * (1 - rep["size_kb"] / rr["size_kb"]), 1) if rr["size_kb"] else None
    return out
