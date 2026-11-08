"""Reference pipeline tests on synthetic images (no network, no PIL): a top-view donut with 'sprinkles'
and a front-view profile. Images are written with bpy at import time."""
import math
import os
import tempfile

import bpy
import numpy as np

D = os.path.join(tempfile.gettempdir(), "blendpro_ref_tests")
os.makedirs(D, exist_ok=True)
TOP = os.path.join(D, "donut_top.png")
FRONT = os.path.join(D, "donut_front.png")
S = 256


def _save(arr, path):
    img = bpy.data.images.new(os.path.basename(path), S, S, alpha=True)
    img.pixels.foreach_set(arr[::-1].astype(np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


yy, xx = np.mgrid[0:S, 0:S].astype(np.float64)
rr = np.hypot(xx - 128, yy - 128)
top = np.ones((S, S, 4)); top[rr < 100, :3] = (0.9, 0.35, 0.55); top[rr < 40, :3] = 1.0
rng = np.random.default_rng(0)
for _ in range(40):  # 40 sprinkles, 9x3 px, random angle, on the ring
    a, rad = rng.uniform(0, 2 * math.pi), rng.uniform(55, 88)
    cx, cy, t = 128 + rad * math.cos(a), 128 + rad * math.sin(a), rng.uniform(0, math.pi)
    u = (xx - cx) * math.cos(t) + (yy - cy) * math.sin(t)
    v = -(xx - cx) * math.sin(t) + (yy - cy) * math.cos(t)
    top[(abs(u) < 4.5) & (abs(v) < 1.5), :3] = rng.choice([(0.1, 0.4, 0.95), (0.98, 0.85, 0.1), (0.2, 0.8, 0.3)])
_save(top, TOP)
front = np.ones((S, S, 4))  # torus side view: ellipse-ish band 200 wide x 80 tall
front[((xx - 128) / 100) ** 2 + ((yy - 128) / 40) ** 2 < 1, :3] = (0.75, 0.45, 0.2)
_save(front, FRONT)


def _measure_top(res):
    assert abs(res["width"] - 0.1) < 0.002, res["width"]
    assert res["hole"] and abs(res["hole"]["width"] - 0.04) < 0.004, res["hole"]
    mid = res["profile"][len(res["profile"]) // 2]
    assert mid["runs"] and len(mid["runs"]) == 2, mid
    return True


def _setup_front(res):
    assert abs(res["silhouette_m"][0] - 0.1) < 0.002 and abs(res["silhouette_m"][1] - 0.04) < 0.003, res
    return True


def _details(res):
    assert 25 <= res["count"] <= 45, res["count"]
    assert res["aspect_median"] > 1.8, res
    return True


def _compare_setup():
    """front reference + a torus that should match it closely"""
    from addon.recipes.reference import reference_image_setup
    reference_image_setup(FRONT, view="front", real_width=0.1, align="bottom")
    bpy.ops.mesh.primitive_torus_add(major_radius=0.03, minor_radius=0.02, location=(0, 0, 0.02))


def _compare_check(res):
    assert res["iou"] > 0.8, res
    return True


CASES = [
    ("reference_measure", {"filepath": TOP, "real_width": 0.1}, _measure_top),
    ("reference_image_setup", {"filepath": FRONT, "view": "front", "real_width": 0.1, "align": "bottom"}, _setup_front),
    ("reference_details", {"filepath": TOP, "real_width": 0.1}, _details),
    ("reference_compare", {"reference": "REF_front"}, _compare_check, _compare_setup),
]
