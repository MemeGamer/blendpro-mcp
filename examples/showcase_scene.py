"""Build the README showcase scene purely from BlendPro recipes (11 calls), then render it.

Run from the repo root with BlendPro installed:  python examples/showcase_scene.py
It starts a headless Blender, so no Blender window is needed."""
import sys, json, os
sys.stdout.reconfigure(encoding="utf-8")
import blendpro.server as s

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "images")
steps = [
    ("terrain_from_noise", dict(name="Ground", size=220, resolution=160, height=2.5, noise_scale=0.03, seed=3)),
    ("procedural_material", dict(preset="concrete", name="M_Ground", target="Ground", color=(0.32, 0.4, 0.22), scale=0.2)),
    ("procedural_building", dict(name="TowerA", floors=7, width=8, depth=7, windows_x=5, windows_y=4, location=(-6, 6, 0))),
    ("procedural_building", dict(name="TowerB", floors=4, width=7, depth=6, windows_x=4, windows_y=4, location=(5, 9, 0))),
    ("low_poly_rock", dict(name="RockSrc", size=0.6, seed=4, location=(0, 0, -50))),
    ("scatter_on_surface", dict(target="Ground", instance="RockSrc", density=0.12, seed=7, scale_min=0.5, scale_max=1.6)),
    ("cable_between_points", dict(start=(-2.0, 4.0, 20.8), end=(1.5, 8.0, 11.8), sag=1.2, radius=0.04, name="Cable")),
    ("sky_world", dict(sun_elevation_deg=18, sun_rotation_deg=-35, strength=0.25, add_sun_light=True)),
    ("frame_objects_camera", dict(targets=["TowerA", "TowerB"], lens=32, margin=0.3, direction=(1.2, -1.9, 1.05))),
    ("set_render_preset", dict(preset="final", engine="EEVEE", resolution=(1600, 900), view_transform="AgX", look="AgX - Punchy", exposure=-1.2)),
]
try:
    s.blender_session("start_headless")
    s.run_python("bpy.ops.wm.read_factory_settings(use_empty=True)", diff=False)
    for name, params in steps:
        if params is None:
            continue
        try:
            r = json.loads(s.recipes("run", name=name, params=params))
            print("OK ", name, json.dumps(r.get("result"))[:160])
        except Exception as e:
            print("ERR", name, str(e)[:400])
    print(s.spatial_report(ascii_map=False)[:900])
    r = json.loads(s.recipes("run", name="render_still", params=dict(filepath=os.path.join(OUT, "showcase.png"))))
    print("RENDER", r.get("result"))
finally:
    s.blender_session("stop_headless")
