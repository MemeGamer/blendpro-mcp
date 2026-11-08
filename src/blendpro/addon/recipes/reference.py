"""Reference-image pipeline: model to match real (or AI-generated) orthographic reference images.

Workflow (see skill/blender/references/modeling/reference-pipeline.md):
  1. reference_measure  - silhouette size/profile/holes + colour palette of each view (text, no image tokens)
  2. reference_image_setup - put the image in the scene as an aligned, scaled overlay (Image empty)
  3. model with real tools (modifiers, proportional edit, shrinkwrap...) using the measured numbers
  4. reference_compare  - render the model's silhouette from the same view and score it against the image
                          (IoU + row-by-row width error), fix, repeat
  5. reference_details  - small surface features (sprinkles, rivets, seeds): count, size spread, colours

Images are read with bpy + numpy (ships with Blender); no PIL needed. Background is detected from the
image border (or alpha), so a plain white / grey / transparent background works best."""
import math
import os
import tempfile

import bpy
import mathutils
import numpy as np

from . import recipe
from ..util import get_obj, r

# view -> (euler degrees for an Image empty / camera facing the viewer, image-right axis, image-up axis)
_VIEWS = {
    "front": ((90, 0, 0), (1, 0, 0), (0, 0, 1)),
    "back": ((90, 0, 180), (-1, 0, 0), (0, 0, 1)),
    "right": ((90, 0, 90), (0, 1, 0), (0, 0, 1)),
    "left": ((90, 0, -90), (0, -1, 0), (0, 0, 1)),
    "top": ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
    "bottom": ((180, 0, 0), (1, 0, 0), (0, -1, 0)),
}


# ------------------------------------------------------------------ image helpers
def _load(filepath):
    path = bpy.path.abspath(filepath)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"reference image not found: {path}")
    img = bpy.data.images.load(path, check_existing=True)
    return img


def _pixels(img, max_size=384):
    """RGBA float array (H, W, 4), row 0 = TOP of the image, downsampled to <= max_size."""
    w, h = img.size
    if not w or not h:
        raise ValueError(f"image {img.name} has no pixels")
    a = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(h, w, 4)[::-1]  # Blender stores bottom row first
    step = max(1, int(math.ceil(max(w, h) / max_size)))
    return a[::step, ::step].copy(), (w, h)


def _fg_mask(px, threshold=None):
    """Foreground mask: alpha if the image has real transparency, else distance from the border colour."""
    alpha = px[..., 3]
    if alpha.min() < 0.5 and (alpha < 0.5).mean() > 0.05:
        return alpha > 0.5, "alpha", None
    rgb = px[..., :3]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(-1))
    if threshold is None:  # border spread + margin, clamped
        bd = np.sqrt(((border - bg) ** 2).sum(-1))
        threshold = float(min(0.25, max(0.06, np.percentile(bd, 95) * 2.5)))
    m = dist > threshold
    # fill pinholes / speculars inside the object: keep bg only where connected to the border
    return _fill_from_border(m), "border-colour", [r(c) for c in bg]


def _flood_bg(mask):
    """Background pixels connected to the image border (4-connectivity), via iterative dilation."""
    bg = ~mask
    reach = np.zeros_like(bg)
    reach[0, :] = bg[0, :]; reach[-1, :] = bg[-1, :]; reach[:, 0] = bg[:, 0]; reach[:, -1] = bg[:, -1]
    for _ in range(mask.shape[0] + mask.shape[1]):
        grow = reach.copy()
        grow[1:] |= reach[:-1]; grow[:-1] |= reach[1:]; grow[:, 1:] |= reach[:, :-1]; grow[:, :-1] |= reach[:, 1:]
        grow &= bg
        if (grow == reach).all():
            break
        reach = grow
    return reach


def _fill_from_border(mask):
    """Close tiny holes (< 0.3% of the object) but keep real holes (donut hole, handle loops)."""
    outside = _flood_bg(mask)
    enclosed = (~mask) & (~outside)
    if not enclosed.any():
        return mask
    labels, n = _label(enclosed)
    out = mask.copy()
    area = mask.sum()
    for i in range(1, n + 1):
        comp = labels == i
        if comp.sum() < 0.003 * area:
            out |= comp
    return out


def _label(mask):
    """Connected components (4-connectivity) without scipy. Returns (labels, count)."""
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    n = 0
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if labels[y0, x0]:
            continue
        n += 1
        stack = [(y0, x0)]
        labels[y0, x0] = n
        while stack:
            y, x = stack.pop()
            for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not labels[yy, xx]:
                    labels[yy, xx] = n
                    stack.append((yy, xx))
    return labels, n


def _srgb_to_lin(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _kmeans(cols, k, iters=12, seed=0):
    if len(cols) == 0:
        return np.zeros((0, 3)), np.zeros(0)
    rng = np.random.default_rng(seed)
    k = min(k, len(cols))
    cent = cols[rng.choice(len(cols), k, replace=False)]
    for _ in range(iters):
        d = ((cols[:, None, :] - cent[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(k):
            sel = cols[lab == j]
            if len(sel):
                cent[j] = sel.mean(0)
    share = np.bincount(lab, minlength=k) / len(cols)
    order = np.argsort(-share)
    return cent[order], share[order]


def _palette(rgb, k):
    if len(rgb) > 20000:
        rgb = rgb[np.random.default_rng(1).choice(len(rgb), 20000, replace=False)]
    cent, share = _kmeans(rgb.astype(np.float64), k)
    out = []
    for c, s in zip(cent, share):
        if s < 0.01:
            continue
        srgb = np.clip(c, 0, 1)
        out.append({"hex": "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in srgb),
                    "linear_rgba": [r(v) for v in _srgb_to_lin(srgb)] + [1.0], "share": r(s, 3)})
    return out


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise ValueError("no foreground found: use a plain/transparent background or pass threshold=")
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _analyse(filepath, threshold=None, max_size=384):
    img = _load(filepath)
    px, (W, H) = _pixels(img, max_size)
    mask, method, bg = _fg_mask(px, threshold)
    x0, y0, x1, y1 = _bbox(mask)
    return img, px, mask, method, bg, (x0, y0, x1, y1), (W, H)


def _scale_from(real_width, real_height, bbox, shape):
    """metres per (downsampled) pixel from a known real width or height of the silhouette."""
    x0, y0, x1, y1 = bbox
    if real_width:
        return real_width / max(1, x1 - x0 + 1)
    if real_height:
        return real_height / max(1, y1 - y0 + 1)
    return None


# ------------------------------------------------------------------ recipes
@recipe("reference", tags=("reference", "image", "measure", "silhouette", "palette", "blueprint"))
def reference_measure(filepath="", real_width=None, real_height=None, rows=12, colors=6, threshold=None,
                      max_size=384) -> dict:
    """Measure one reference view as TEXT: silhouette bbox, aspect, row-by-row width profile, holes, palette.
    filepath: image (front/left/top... orthographic works best, plain or transparent background).
    real_width / real_height: the real size of the object's silhouette in metres (e.g. donut 0.10) - all
    lengths are then returned in metres; otherwise as fractions of the silhouette width.
    profile: `rows` evenly spaced rows from top to bottom of the silhouette: width, left/right edges and the
    filled runs (2 runs on a row = there is a gap/hole there, e.g. a donut hole in top view).
    palette: k-means colours of the object (hex + linear RGBA ready for a Principled Base Color), and
    bands: dominant colour of the top / middle / bottom third (e.g. glaze vs dough in a front view)."""
    img, px, mask, method, bg, (x0, y0, x1, y1), (W, H) = _analyse(filepath, threshold, max_size)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    mpp = _scale_from(real_width, real_height, (x0, y0, x1, y1), mask.shape)
    unit = "m" if mpp else "frac_of_width"
    L = (lambda px_: r(px_ * mpp, 4)) if mpp else (lambda px_: r(px_ / bw, 4))
    cx = (x0 + x1) / 2
    prof = []
    for i in range(rows):
        y = int(round(y0 + (bh - 1) * (i + 0.5) / rows))
        row = mask[y, x0:x1 + 1]
        idx = np.nonzero(row)[0]
        if not len(idx):
            prof.append({"v": r((i + 0.5) / rows, 3), "width": 0}); continue
        runs, start = [], idx[0]
        for a, b in zip(idx[:-1], idx[1:]):
            if b != a + 1:
                runs.append((start, a)); start = b
        runs.append((start, idx[-1]))
        prof.append({"v": r((i + 0.5) / rows, 3), "width": L(idx[-1] - idx[0] + 1),
                     "left": L(x0 + idx[0] - cx), "right": L(x0 + idx[-1] - cx),
                     "runs": [[L(x0 + a - cx), L(x0 + b - cx)] for a, b in runs] if len(runs) > 1 else None})
    outside = _flood_bg(mask)
    holes = (~mask) & (~outside)
    hole = None
    if holes.any():
        hx0, hy0, hx1, hy1 = _bbox(holes)
        hole = {"width": L(hx1 - hx0 + 1), "height": L(hy1 - hy0 + 1),
                "center_offset": [L((hx0 + hx1) / 2 - cx), L((y0 + y1) / 2 - (hy0 + hy1) / 2)],
                "area_ratio": r(holes.sum() / max(1, mask.sum() + holes.sum()), 3)}
    rgb = px[..., :3][mask]
    bands = []
    for name, (a, b) in (("top", (0, 1 / 3)), ("middle", (1 / 3, 2 / 3)), ("bottom", (2 / 3, 1))):
        ya, yb = int(y0 + a * bh), int(y0 + b * bh)
        sub = px[ya:yb, ..., :3][mask[ya:yb]]
        pal = _palette(sub, 2)
        bands.append({"band": name, "dominant": pal[0] if pal else None})
    return {"image": img.name, "size_px": [W, H], "background": method, "bg_color": bg,
            "bbox_frac": [r(x0 / mask.shape[1], 3), r(y0 / mask.shape[0], 3), r((x1 + 1) / mask.shape[1], 3),
                          r((y1 + 1) / mask.shape[0], 3)],
            "aspect_w_over_h": r(bw / bh, 3), "unit": unit,
            "width": L(bw), "height": L(bh), "fill_ratio": r(mask.sum() / (bw * bh), 3),
            "hole": hole, "profile": prof, "palette": _palette(rgb, colors), "bands": bands}


@recipe("reference", tags=("reference", "image", "empty", "overlay", "blueprint", "trace"))
def reference_image_setup(filepath="", view="front", real_width=None, real_height=None, center=(0, 0, 0),
                          align="center", opacity=0.5, depth="DEFAULT", only_axis_aligned=False,
                          name=None, threshold=None) -> dict:
    """Put a reference image in the scene as an Image empty, aligned to a view and scaled to real size.
    view: front | back | left | right | top | bottom (front looks along +Y, top looks down -Z).
    real_width / real_height (m): scale so the image's SILHOUETTE (not the whole canvas) has this size.
    center: where the silhouette lands; align='bottom' puts the silhouette's bottom edge on center.z
    (front/side views) so the object sits on the ground. depth: DEFAULT | FRONT (overlay on top of the mesh,
    good for tracing) | BACK. only_axis_aligned: show only when the viewport looks straight down that view.
    The empty stores ref_* custom properties that reference_compare reads."""
    if view not in _VIEWS:
        raise ValueError(f"view must be one of {list(_VIEWS)}")
    img, px, mask, method, bg, (x0, y0, x1, y1), (W, H) = _analyse(filepath, threshold)
    mh, mw = mask.shape
    # world size of the whole canvas
    if real_width:
        canvas_w = real_width * mw / (x1 - x0 + 1)
    elif real_height:
        canvas_w = real_height * mh / (y1 - y0 + 1) * (mw / mh)
    else:
        canvas_w = 1.0
    canvas_h = canvas_w * mh / mw
    rot, right, up = _VIEWS[view]
    right, up = mathutils.Vector(right), mathutils.Vector(up)
    # silhouette centre relative to canvas centre, in metres (image y grows downward)
    sx = ((x0 + x1 + 1) / 2 / mw - 0.5) * canvas_w
    sy = (0.5 - (y0 + y1 + 1) / 2 / mh) * canvas_h
    loc = mathutils.Vector(center) - right * sx - up * sy
    if align == "bottom" and view not in ("top", "bottom"):
        loc += up * ((y1 - y0 + 1) / mh * canvas_h / 2)
    name = name or f"REF_{view}"
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = "IMAGE"
    e.data = img
    e.empty_display_size = max(canvas_w, canvas_h)
    e.empty_image_offset = (-0.5, -0.5)
    e.location = loc
    e.rotation_euler = [math.radians(a) for a in rot]
    e.empty_image_depth = depth
    e.use_empty_image_alpha = opacity < 1
    e.color[3] = opacity
    e.show_empty_image_only_axis_aligned = only_axis_aligned
    e.show_empty_image_orthographic = True
    e.show_empty_image_perspective = not only_axis_aligned
    e.hide_select = True
    e.hide_render = True
    coll = bpy.data.collections.get("References") or bpy.data.collections.new("References")
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    coll.objects.link(e)
    e["ref_view"] = view
    e["ref_path"] = bpy.path.abspath(filepath)
    e["ref_canvas"] = [canvas_w, canvas_h]
    e["ref_threshold"] = -1.0 if threshold is None else float(threshold)
    return {"empty": e.name, "view": view, "location": [r(v, 4) for v in loc], "canvas_m": [r(canvas_w, 4), r(canvas_h, 4)],
            "silhouette_m": [r((x1 - x0 + 1) / mw * canvas_w, 4), r((y1 - y0 + 1) / mh * canvas_h, 4)],
            "background": method, "depth": depth, "opacity": opacity}


@recipe("reference", tags=("reference", "compare", "iou", "silhouette", "match", "verify"))
def reference_compare(reference="REF_front", objects=None, rows=10, resolution=256) -> dict:
    """Score the model against a reference empty made by reference_image_setup: renders the objects'
    silhouette with an orthographic camera that frames exactly the reference canvas, then compares masks.
    objects: names to include (default: all visible meshes/curves). Returns iou (1.0 = perfect), area_ratio
    (model/ref), centroid offset (m) and per-row width error (m, +=model too wide) with the worst rows, so you
    know WHERE to fix. Restores every render setting it touches."""
    e = get_obj(reference)
    if "ref_view" not in e:
        raise ValueError(f"{reference} was not made by reference_image_setup")
    view = e["ref_view"]
    canvas_w, canvas_h = e["ref_canvas"]
    thr = e.get("ref_threshold", -1.0)
    img, px, ref_mask, *_ = _analyse(e["ref_path"], None if thr < 0 else thr, resolution)
    mh, mw = ref_mask.shape
    sc = bpy.context.scene
    keep = set(objects) if objects else None
    targets = [o for o in sc.objects if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}
               and (keep is None and o.visible_get() or (keep is not None and o.name in keep))]
    if not targets:
        raise ValueError("no objects to compare")
    # --- temporary ortho camera on the reference canvas
    cam_data = bpy.data.cameras.new("_ref_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(canvas_w, canvas_h)
    cam_data.clip_start = 0.001
    cam_data.clip_end = 100 * max(canvas_w, canvas_h) + 100
    cam = bpy.data.objects.new("_ref_cam", cam_data)
    sc.collection.objects.link(cam)
    cam.rotation_euler = e.rotation_euler
    normal = e.matrix_world.to_3x3() @ mathutils.Vector((0, 0, 1))
    cam.location = e.matrix_world.translation + normal.normalized() * 50 * max(canvas_w, canvas_h)
    saved = {"camera": sc.camera, "engine": sc.render.engine, "rx": sc.render.resolution_x,
             "ry": sc.render.resolution_y, "pct": sc.render.resolution_percentage,
             "film": sc.render.film_transparent, "path": sc.render.filepath,
             "fmt": sc.render.image_settings.file_format, "cm": sc.render.image_settings.color_mode,
             "light": sc.display.shading.light, "ctype": sc.display.shading.color_type,
             "hide": {o.name: o.hide_render for o in sc.objects}}
    tmp = os.path.join(tempfile.gettempdir(), "blendpro_refcmp.png")
    try:
        for o in sc.objects:
            o.hide_render = o not in targets and o is not cam
        sc.camera = cam
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = mw, mh, 100
        sc.render.film_transparent = True
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGBA"
        sc.display.shading.light = "FLAT"
        sc.display.shading.color_type = "SINGLE"
        sc.render.filepath = tmp
        bpy.ops.render.render(write_still=True)
    finally:
        sc.camera = saved["camera"]; sc.render.engine = saved["engine"]
        sc.render.resolution_x, sc.render.resolution_y = saved["rx"], saved["ry"]
        sc.render.resolution_percentage = saved["pct"]; sc.render.film_transparent = saved["film"]
        sc.render.filepath = saved["path"]; sc.render.image_settings.file_format = saved["fmt"]
        sc.render.image_settings.color_mode = saved["cm"]
        sc.display.shading.light = saved["light"]; sc.display.shading.color_type = saved["ctype"]
        for n, h in saved["hide"].items():
            if n in sc.objects:
                sc.objects[n].hide_render = h
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    rimg = bpy.data.images.load(tmp, check_existing=False)
    try:
        rpx, _ = _pixels(rimg, max(mw, mh) + 1)
    finally:
        bpy.data.images.remove(rimg)
    model = rpx[..., 3] > 0.5
    if model.shape != ref_mask.shape:  # rounding guard
        h, w = min(model.shape[0], mh), min(model.shape[1], mw)
        model, ref_mask = model[:h, :w], ref_mask[:h, :w]
    inter, union = (model & ref_mask).sum(), (model | ref_mask).sum()
    mpp = canvas_w / ref_mask.shape[1]

    def centroid(m):
        ys, xs = np.nonzero(m)
        return (xs.mean(), ys.mean()) if len(xs) else (0, 0)
    (mx, my), (rx, ry) = centroid(model), centroid(ref_mask)
    rb = _bbox(ref_mask)
    errs = []
    for i in range(rows):
        y = int(round(rb[1] + (rb[3] - rb[1]) * (i + 0.5) / rows))
        wr = ref_mask[y].sum() * mpp
        wm = model[y].sum() * mpp
        errs.append({"v": r((i + 0.5) / rows, 3), "ref_width": r(wr, 4), "model_width": r(wm, 4), "error": r(wm - wr, 4)})
    worst = sorted(errs, key=lambda d: -abs(d["error"]))[:3]
    iou = inter / union if union else 0.0
    return {"reference": e.name, "view": view, "iou": r(iou, 3),
            "verdict": "match" if iou > 0.92 else "close" if iou > 0.85 else "off",
            "area_ratio": r(model.sum() / max(1, ref_mask.sum()), 3),
            "centroid_offset_m": [r((mx - rx) * mpp, 4), r((ry - my) * mpp, 4)],
            "rows": errs, "worst_rows": worst, "objects": [o.name for o in targets]}


@recipe("reference", tags=("reference", "details", "sprinkles", "decals", "scatter", "palette"))
def reference_details(filepath="", real_width=None, base_colors=4, min_px=6, max_frac=0.02, threshold=0.3,
                      colors=6, edge_margin=0.03, max_size=512) -> dict:
    """Find small surface details in a reference (sprinkles, seeds, rivets, spots) and describe them as numbers
    for a scatter: count, density, length/width spread, orientation spread and their colour palette.
    Method: silhouette -> the `base_colors` dominant surface colours (k-means) -> pixels far from all of them
    (> threshold) are details -> connected blobs between min_px and max_frac of the object area -> PCA size.
    edge_margin (fraction of the silhouette width): ignore blobs near the outline or a hole edge, where the
    side of the object (dough rim, rounded edge) shows through in a top view.
    Tuned on a GPT top view of a sprinkled donut: base_colors=4, threshold=0.3 found the 6 sprinkle colours.
    real_width (m) of the silhouette turns sizes into metres. Use the result to build several detail
    variants (lengths, colours, slight bend) instead of one repeated instance."""
    img, px, mask, method, bg, (x0, y0, x1, y1), (W, H) = _analyse(filepath, None, max_size)
    rgb = px[..., :3]
    base, _ = _kmeans(rgb[mask].astype(np.float64)[:20000], base_colors)
    d = np.min(np.sqrt(((rgb[..., None, :] - base[None, None]) ** 2).sum(-1)), axis=-1)
    det = mask & (d > threshold)
    inner = mask.copy()
    for _ in range(max(0, int(round(edge_margin * (x1 - x0 + 1))))):  # erode
        inner[1:] &= inner[:-1].copy(); inner[:-1] &= inner[1:].copy()
        inner[:, 1:] &= inner[:, :-1].copy(); inner[:, :-1] &= inner[:, 1:].copy()
    labels, n = _label(det)
    area = mask.sum()
    mpp = (real_width / (x1 - x0 + 1)) if real_width else None
    blobs, cols = [], []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(labels == i)
        if len(xs) < min_px or len(xs) > max_frac * area:
            continue
        if not inner[int(ys.mean()), int(xs.mean())]:
            continue
        pts = np.stack([xs, ys], 1).astype(np.float64)
        pts -= pts.mean(0)
        cov = np.cov(pts.T) if len(pts) > 2 else np.eye(2)
        ev, evec = np.linalg.eigh(cov)
        length = 4 * math.sqrt(max(ev[1], 1e-6))
        width = 4 * math.sqrt(max(ev[0], 1e-6))
        ang = math.degrees(math.atan2(evec[1, 1], evec[0, 1])) % 180
        blobs.append((length, width, ang))
        cols.append(rgb[ys, xs].mean(0))
    if not blobs:
        return {"image": img.name, "count": 0, "hint": "no details found - lower threshold or min_px"}
    b = np.array(blobs)
    conv = (lambda v: r(v * mpp, 5)) if mpp else (lambda v: r(v / (x1 - x0 + 1), 4))
    pal = _palette(np.array(cols), colors)
    surf_m2 = area * mpp * mpp if mpp else None
    return {"image": img.name, "unit": "m" if mpp else "frac_of_width", "count": len(blobs),
            "density_per_m2": r(len(blobs) / surf_m2, 1) if surf_m2 else None,
            "length": {"median": conv(np.median(b[:, 0])), "p10": conv(np.percentile(b[:, 0], 10)),
                       "p90": conv(np.percentile(b[:, 0], 90))},
            "width": {"median": conv(np.median(b[:, 1])), "p10": conv(np.percentile(b[:, 1], 10)),
                      "p90": conv(np.percentile(b[:, 1], 90))},
            "aspect_median": r(float(np.median(b[:, 0] / np.maximum(b[:, 1], 1e-6))), 2),
            "orientation_spread_deg": r(float(np.std(b[:, 2])), 1),
            "palette": pal, "base_surface": [("#%02x%02x%02x" % tuple(int(round(v * 255)) for v in np.clip(c, 0, 1))) for c in base]}
