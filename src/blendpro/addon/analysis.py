"""Mesh topology analysis and spatial relationship reports (runs inside Blender)."""
import math
import bpy
import bmesh
import mathutils
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from .util import r, get_obj, world_bbox

TARGETS = {
    # rough per-asset budgets; scene budgets are the user's call
    "quest": {"tris": 50000, "materials": 3, "texture": 2048},
    "webxr": {"tris": 50000, "materials": 3, "texture": 2048},
    "mobile": {"tris": 20000, "materials": 2, "texture": 1024},
    "unity": {"tris": 150000, "materials": 6, "texture": 4096},
    "film": {"tris": None, "materials": None, "texture": None},
}


def _bm_from(obj, evaluated):
    dg = bpy.context.evaluated_depsgraph_get()
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    bm = bmesh.new()
    if evaluated:
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        bm.from_mesh(me)
        ev.to_mesh_clear()
    else:
        bm.from_mesh(obj.data)
    return bm


def mesh_analysis(names, evaluated=True, target=None, self_intersections=True, details=False):
    if isinstance(names, str):
        names = [names]
    return {n: _analyze(get_obj(n), evaluated, target, self_intersections, details) for n in names}


def _analyze(obj, evaluated, target, self_intersections, details):
    if obj.type != "MESH":
        return {"error": f"{obj.name} is {obj.type}, not MESH"}
    bm = _bm_from(obj, evaluated)
    try:
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        nv, ne, nf = len(bm.verts), len(bm.edges), len(bm.faces)
        tris = quads = ngons = 0
        ngon_idx = []
        for f in bm.faces:
            k = len(f.verts)
            if k == 3:
                tris += 1
            elif k == 4:
                quads += 1
            else:
                ngons += 1
                if len(ngon_idx) < 20:
                    ngon_idx.append(f.index)
        tri_count = sum(len(f.verts) - 2 for f in bm.faces)
        boundary = wire = multi = flipped_edges = zero_len = 0
        nm_idx = []
        for e in bm.edges:
            lf = len(e.link_faces)
            if lf == 0:
                wire += 1
            elif lf == 1:
                boundary += 1
            elif lf > 2:
                multi += 1
                if len(nm_idx) < 20:
                    nm_idx.append(e.index)
            elif not e.is_contiguous:
                flipped_edges += 1
            if e.calc_length() < 1e-6:
                zero_len += 1
        loose_verts = sum(1 for v in bm.verts if not v.link_edges)
        degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-10)
        poles3 = poles5 = poles6 = 0
        for v in bm.verts:
            if v.is_boundary or not v.is_manifold:
                continue
            val = len(v.link_edges)
            if val == 3:
                poles3 += 1
            elif val == 5:
                poles5 += 1
            elif val >= 6:
                poles6 += 1
        size = max(obj.dimensions) or 1.0
        eps = max(size * 1e-5, 1e-6)
        dup = None
        if nv <= 300000:
            kd = KDTree(nv)
            for v in bm.verts:
                kd.insert(v.co, v.index)
            kd.balance()
            seen = set()
            dup = 0
            for v in bm.verts:
                if v.index in seen:
                    continue
                hits = kd.find_range(v.co, eps)
                if len(hits) > 1:
                    dup += len(hits) - 1
                    seen.update(h[1] for h in hits)
        # symmetry across local X
        sym = None
        if 0 < nv <= 200000:
            kd2 = KDTree(nv)
            for v in bm.verts:
                kd2.insert(v.co, v.index)
            kd2.balance()
            tol = size * 1e-3
            hit = sum(1 for v in bm.verts if kd2.find(mathutils.Vector((-v.co.x, v.co.y, v.co.z)))[2] <= tol)
            sym = round(hit / nv, 3)
        closed = boundary == 0 and wire == 0 and multi == 0
        volume = None
        inside_out = None
        if closed and nf:
            try:
                volume = bm.calc_volume(signed=True)
                inside_out = volume < 0
                volume = r(abs(volume), 5)
            except Exception:
                pass
        isect = None
        if self_intersections and 0 < nf <= 60000:
            tree = BVHTree.FromBMesh(bm, epsilon=0.0)
            pairs = tree.overlap(tree)
            count = 0
            for a, b in pairs:
                if a >= b:
                    continue
                fa, fb = bm.faces[a], bm.faces[b]
                if set(fa.verts) & set(fb.verts):
                    continue
                count += 1
            isect = count
        # UVs
        uv = {"layers": [l.name for l in obj.data.uv_layers]}
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is not None:
            zero_uv = 0
            outside = 0
            for f in bm.faces:
                pts = [l[uv_layer].uv for l in f.loops]
                a = 0.0
                for i in range(len(pts)):
                    x1, y1 = pts[i]
                    x2, y2 = pts[(i + 1) % len(pts)]
                    a += x1 * y2 - x2 * y1
                if abs(a) < 1e-12:
                    zero_uv += 1
                if any(p.x < -1e-4 or p.x > 1.0001 or p.y < -1e-4 or p.y > 1.0001 for p in pts):
                    outside += 1
            uv["zero_area_faces"] = zero_uv
            uv["faces_outside_0_1"] = outside
            uv["islands"] = _uv_islands(bm, uv_layer)
        # material usage
        mat_use = {}
        for f in bm.faces:
            mat_use[f.material_index] = mat_use.get(f.material_index, 0) + 1
        slots = [s.material.name if s.material else None for s in obj.material_slots]
        smooth = sum(1 for f in bm.faces if f.smooth)
        res = {
            "evaluated": evaluated,
            "counts": {"verts": nv, "edges": ne, "faces": nf, "tris": tri_count, "tri_faces": tris, "quads": quads, "ngons": ngons},
            "topology": {"boundary_edges": boundary, "wire_edges": wire, "non_manifold_edges(>2 faces)": multi,
                         "inconsistent_normal_edges": flipped_edges, "loose_verts": loose_verts,
                         "degenerate_faces": degenerate, "zero_length_edges": zero_len,
                         "duplicate_verts": dup, "poles": {"3": poles3, "5": poles5, "6+": poles6},
                         "closed": closed, "inside_out": inside_out, "volume": volume,
                         "self_intersecting_face_pairs": isect, "x_symmetry": sym},
            "shading": {"smooth_faces": smooth, "flat_faces": nf - smooth},
            "uv": uv,
            "materials": {"slots": slots, "faces_per_slot": {str(k): v for k, v in sorted(mat_use.items())}},
            "transform": {"scale": r(obj.scale, 4), "rotation_applied": all(abs(a) < 1e-6 for a in obj.rotation_euler),
                          "negative_scale": obj.matrix_world.is_negative,
                          "dimensions": r(obj.dimensions, 4)},
        }
        bb = world_bbox(obj, bpy.context.evaluated_depsgraph_get())
        if bb:
            center = (bb[0] + bb[1]) / 2
            loc = obj.matrix_world.translation
            res["transform"]["origin_vs_bbox"] = {"origin": r(loc, 3), "bbox_center": r(center, 3), "bbox_min_z": r(bb[0].z, 3)}
        if details:
            res["sample_indices"] = {"ngon_faces": ngon_idx, "non_manifold_edges": nm_idx}
        res["issues"] = _issues(res, obj, target)
        return res
    finally:
        bm.free()


def _uv_islands(bm, uv_layer, cap=20000):
    if len(bm.faces) > cap:
        return None
    # union faces sharing an edge with matching UVs at both ends
    parent = list(range(len(bm.faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for e in bm.edges:
        if len(e.link_faces) != 2:
            continue
        f1, f2 = e.link_faces
        def uvs(f):
            d = {}
            for l in f.loops:
                d[l.vert.index] = l[uv_layer].uv.copy()
            return d
        u1, u2 = uvs(f1), uvs(f2)
        if all((u1[v.index] - u2[v.index]).length < 1e-5 for v in e.verts):
            a, b = find(f1.index), find(f2.index)
            if a != b:
                parent[a] = b
    return len({find(i) for i in range(len(bm.faces))})


def _issues(res, obj, target):
    t = res["topology"]
    c = res["counts"]
    out = []
    if any(abs(s - 1) > 1e-4 for s in obj.scale):
        out.append("unapplied scale -> Ctrl+A Apply Scale (bpy: object.transform_apply(scale=True)); breaks bevels/sculpt/export")
    if res["transform"]["negative_scale"]:
        out.append("negative scale -> apply scale then recalc normals")
    if c["ngons"]:
        out.append(f"{c['ngons']} n-gons -> triangulate/quadrangulate or fix before SubD/export")
    if t["duplicate_verts"]:
        out.append(f"{t['duplicate_verts']} duplicate verts -> mesh.remove_doubles (Merge by Distance)")
    if t["inconsistent_normal_edges"]:
        out.append(f"{t['inconsistent_normal_edges']} edges with inconsistent winding -> mesh.normals_make_consistent(inside=False)")
    if t["inside_out"]:
        out.append("closed mesh is inside-out (negative volume) -> flip normals")
    if t["non_manifold_edges(>2 faces)"]:
        out.append(f"{t['non_manifold_edges(>2 faces)']} edges shared by >2 faces -> internal faces; select_non_manifold and delete")
    if t["wire_edges"] or t["loose_verts"]:
        out.append(f"loose geometry ({t['wire_edges']} wire edges, {t['loose_verts']} verts) -> mesh.delete_loose")
    if t["degenerate_faces"] or t["zero_length_edges"]:
        out.append(f"degenerate geometry ({t['degenerate_faces']} faces, {t['zero_length_edges']} edges) -> mesh.dissolve_degenerate")
    if t["self_intersecting_face_pairs"]:
        out.append(f"{t['self_intersecting_face_pairs']} self-intersecting face pairs (fine for renders; bad for booleans/3D print/remesh)")
    if t["poles"]["6+"]:
        out.append(f"{t['poles']['6+']} high-valence poles (6+) -> pinching under SubD")
    if not res["uv"]["layers"]:
        out.append("no UV map -> needed for textures/baking/export")
    elif res["uv"].get("zero_area_faces"):
        out.append(f"{res['uv']['zero_area_faces']} faces with zero UV area -> unwrap")
    if None in res["materials"]["slots"]:
        out.append("empty material slot")
    if target:
        b = TARGETS.get(target.lower())
        if b is None:
            out.append(f"unknown target {target}; known: {list(TARGETS)}")
        else:
            if b["tris"] and c["tris"] > b["tris"]:
                out.append(f"[{target}] {c['tris']} tris > {b['tris']} budget -> Decimate/LOD/retopo")
            nm = len([s for s in res["materials"]["slots"] if s])
            if b["materials"] and nm > b["materials"]:
                out.append(f"[{target}] {nm} materials (draw calls) > {b['materials']} -> atlas/merge")
            if b["texture"]:
                for s in obj.material_slots:
                    m = s.material
                    if m and m.node_tree:
                        for n in m.node_tree.nodes:
                            if n.bl_idname == "ShaderNodeTexImage" and n.image and max(n.image.size) > b["texture"]:
                                out.append(f"[{target}] texture {n.image.name} {tuple(n.image.size)} > {b['texture']}")
    return out


# ---------------------------------------------------------------- spatial

def spatial_report(names=None, ground_z=0.0, ascii_map=True, map_size=(56, 22), mesh_intersections=True):
    scene = bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    objs = [get_obj(n) for n in names] if names else [o for o in scene.objects if o.visible_get()]
    boxes = {}
    for o in objs:
        bb = world_bbox(o, dg)
        if bb:
            boxes[o.name] = bb
    if not boxes:
        return {"note": "no objects with geometry"}
    allmin = mathutils.Vector((min(b[0].x for b in boxes.values()), min(b[0].y for b in boxes.values()), min(b[0].z for b in boxes.values())))
    allmax = mathutils.Vector((max(b[1].x for b in boxes.values()), max(b[1].y for b in boxes.values()), max(b[1].z for b in boxes.values())))
    scale = max((allmax - allmin).length, 1e-6)
    tol = max(scale * 0.002, 1e-4)
    report = {"scene_bounds": [r(allmin, 3), r(allmax, 3)], "objects": {}}

    def xy_overlap(a, b):
        return a[0].x < b[1].x - tol and b[0].x < a[1].x - tol and a[0].y < b[1].y - tol and b[0].y < a[1].y - tol

    names_sorted = sorted(boxes)
    for n in names_sorted:
        mn, mx = boxes[n]
        entry = {"min": r(mn, 3), "max": r(mx, 3), "center": r((mn + mx) / 2, 3), "size": r(mx - mn, 3)}
        support = None
        if abs(mn.z - ground_z) <= tol:
            support = "ground"
        else:
            best = None
            for m in names_sorted:
                if m == n:
                    continue
                omn, omx = boxes[m]
                if abs(mn.z - omx.z) <= tol and xy_overlap(boxes[n], boxes[m]):
                    best = m
                    break
            if best:
                support = f"on {best}"
            elif mn.z < ground_z - tol:
                support = f"below ground by {r(ground_z - mn.z, 3)}"
            else:
                # nearest thing underneath
                below = [(mn.z - boxes[m][1].z, m) for m in names_sorted if m != n and boxes[m][1].z <= mn.z + tol and xy_overlap(boxes[n], boxes[m])]
                if below:
                    gap, m = min(below)
                    support = f"floating {r(gap, 3)} above {m}"
                else:
                    support = f"floating {r(mn.z - ground_z, 3)} above ground"
        entry["support"] = support
        report["objects"][n] = entry
    # pairwise overlaps
    overlaps = []
    bvh_cache = {}

    def bvh(name):
        if name not in bvh_cache:
            o = bpy.data.objects[name]
            if o.type != "MESH":
                bvh_cache[name] = None
            else:
                ev = o.evaluated_get(dg)
                me = ev.to_mesh()
                mw = o.matrix_world
                verts = [mw @ v.co for v in me.vertices]
                polys = [tuple(p.vertices) for p in me.polygons]
                ev.to_mesh_clear()
                bvh_cache[name] = BVHTree.FromPolygons(verts, polys) if polys and len(polys) < 200000 else None
        return bvh_cache[name]
    checked = 0
    for i, a in enumerate(names_sorted):
        for b in names_sorted[i + 1:]:
            A, B = boxes[a], boxes[b]
            ox = min(A[1].x, B[1].x) - max(A[0].x, B[0].x)
            oy = min(A[1].y, B[1].y) - max(A[0].y, B[0].y)
            oz = min(A[1].z, B[1].z) - max(A[0].z, B[0].z)
            if ox > tol and oy > tol and oz > tol:
                oa, ob = bpy.data.objects[a], bpy.data.objects[b]
                if oa.parent == ob or ob.parent == oa:
                    continue
                item = {"pair": [a, b], "bbox_overlap": r([ox, oy, oz], 3)}
                if mesh_intersections and checked < 60:
                    ta, tb = bvh(a), bvh(b)
                    if ta and tb:
                        checked += 1
                        item["surfaces_intersect"] = len(ta.overlap(tb)) > 0
                overlaps.append(item)
    if overlaps:
        report["overlaps"] = overlaps[:80]
    # camera framing
    cam = scene.camera
    if cam:
        from bpy_extras.object_utils import world_to_camera_view
        cinfo = {"name": cam.name, "location": r(cam.matrix_world.translation, 3),
                 "looking": r(cam.matrix_world.to_quaternion() @ mathutils.Vector((0, 0, -1)), 3)}
        framing = {}
        for n in names_sorted:
            mn, mx = boxes[n]
            corners = [mathutils.Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]
            pts = [world_to_camera_view(scene, cam, c) for c in corners]
            if all(p.z <= 0 for p in pts):
                framing[n] = "behind camera"
                continue
            xs = [p.x for p in pts if p.z > 0]
            ys = [p.y for p in pts if p.z > 0]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            if x1 < 0 or x0 > 1 or y1 < 0 or y0 > 1:
                framing[n] = "out of frame"
                continue
            vis_w = max(0.0, min(x1, 1) - max(x0, 0))
            vis_h = max(0.0, min(y1, 1) - max(y0, 0))
            cut = x0 < 0 or x1 > 1 or y0 < 0 or y1 > 1
            framing[n] = f"screen x{r(x0, 2)}-{r(x1, 2)} y{r(y0, 2)}-{r(y1, 2)} ~{round(vis_w * vis_h * 100, 1)}% of frame" + (" (cropped by frame edge)" if cut else "")
        cinfo["framing"] = framing
        report["camera"] = cinfo
    if ascii_map:
        report["top_view_map"] = _ascii_map(boxes, allmin, allmax, map_size, ("x", "y"))
        report["front_view_map"] = _ascii_map(boxes, allmin, allmax, (map_size[0], max(8, map_size[1] // 2)), ("x", "z"))
    return report


_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _ascii_map(boxes, allmin, allmax, size, axes):
    W, H = size
    ax, ay = axes
    x0, x1 = getattr(allmin, ax), getattr(allmax, ax)
    y0, y1 = getattr(allmin, ay), getattr(allmax, ay)
    sx = (x1 - x0) or 1.0
    sy = (y1 - y0) or 1.0
    grid = [["." for _ in range(W)] for _ in range(H)]
    legend = {}
    # draw larger objects first so small ones stay visible
    order = sorted(boxes, key=lambda n: -((getattr(boxes[n][1], ax) - getattr(boxes[n][0], ax)) * (getattr(boxes[n][1], ay) - getattr(boxes[n][0], ay))))
    for i, n in enumerate(order[: len(_CHARS)]):
        ch = _CHARS[i]
        legend[ch] = n
        mn, mx = boxes[n]
        c0 = int((getattr(mn, ax) - x0) / sx * (W - 1))
        c1 = int(round((getattr(mx, ax) - x0) / sx * (W - 1)))
        r0 = int((getattr(mn, ay) - y0) / sy * (H - 1))
        r1 = int(round((getattr(mx, ay) - y0) / sy * (H - 1)))
        for rr in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                grid[H - 1 - rr][cc] = ch
    rows = ["".join(row) for row in grid]
    header = f"{ax.upper()} -> [{r(x0, 2)}..{r(x1, 2)}], {ay.upper()} ^ [{r(y0, 2)}..{r(y1, 2)}]; later/smaller objects drawn on top"
    return {"axes": header, "rows": rows, "legend": legend}
