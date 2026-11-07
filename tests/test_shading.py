"""Tests for recipes/shading.py (run via tests/run_recipe_tests.py -- shading)."""
import os
import tempfile

import bpy

TMP = tempfile.mkdtemp(prefix="bp_shading_")
TEX = os.path.join(TMP, "tex")
os.makedirs(TEX, exist_ok=True)


def _make_png(path, color):
    img = bpy.data.images.new("tmp_" + os.path.basename(path), 8, 8)
    img.pixels[:] = list(color) * 64
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


for fname, col in (("Brick_BaseColor.png", (0.6, 0.2, 0.1, 1)), ("Brick_Normal_DX.png", (0.5, 0.5, 1, 1)),
                   ("Brick_Roughness.png", (0.7, 0.7, 0.7, 1)), ("Brick_AO.png", (0.9, 0.9, 0.9, 1)),
                   ("Brick_Height.png", (0.5, 0.5, 0.5, 1)), ("readme_preview_thumb.png", (1, 1, 1, 1))):
    _make_png(os.path.join(TEX, fname), col)


def _mat(name):
    return bpy.data.materials[name]


def _types(mat_name):
    return {n.bl_idname for n in _mat(mat_name).node_tree.nodes}


def _linked(mat_name, node_type, socket):
    for n in _mat(mat_name).node_tree.nodes:
        if n.bl_idname == node_type and n.inputs[socket].is_linked:
            return True
    return False


def _check_pbr(res):
    m = _mat("Brick")
    imgs = {n.label: n.image for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"}
    assert set(res["maps"]) == {"basecolor", "normal", "roughness", "ao", "height"}, res["maps"]
    assert imgs["basecolor"].colorspace_settings.name == "sRGB"
    assert imgs["normal"].colorspace_settings.name == "Non-Color"
    assert imgs["roughness"].colorspace_settings.name == "Non-Color"
    assert res["directx_normal"] is True and "ShaderNodeCombineColor" in _types("Brick")
    assert _linked("Brick", "ShaderNodeBsdfPrincipled", "Normal")
    assert _linked("Brick", "ShaderNodeOutputMaterial", "Displacement")
    assert "readme_preview_thumb.png" in res["ignored"]
    return True


def _check_procedural(res):
    from addon.recipes import REGISTRY
    assert "ShaderNodeTexWave" in _types(res["material"])
    assert _linked(res["material"], "ShaderNodeBsdfPrincipled", "Base Color")
    for p in ("brushed_metal", "polished_metal", "concrete", "car_paint", "plastic", "rubber"):
        out = REGISTRY["procedural_material"]["fn"](preset=p, name="T_" + p)
        assert out["links"] >= 2, (p, out)
    assert _mat("T_car_paint").node_tree.nodes["Principled BSDF"].inputs["Coat Weight"].default_value == 1.0
    return bpy.data.objects["Plank"].active_material.name == res["material"]


def _check_glass(res):
    m = _mat("Pane")
    b = m.node_tree.nodes["Principled BSDF"]
    return (b.inputs["Transmission Weight"].default_value == 1.0 and b.inputs["Thin Wall"].default_value
            and m.thickness_mode == "SLAB" and m.use_raytrace_refraction)


def _check_report(res):
    names = {m["name"] for m in res["materials"]}
    assert {"Demo_Used", "Demo_Unused", "Demo_MissingTex"} <= names
    assert "Demo_Unused" in res["unused"]
    assert any(x["material"] == "Demo_MissingTex" for x in res["missing_textures"])
    assert "ReportDemoNoMat" in res["objects_without_material"]
    assert "Demo_Unused" in res["purged"]["materials"] and "Demo_Unused" not in bpy.data.materials
    return "Demo_Used" in bpy.data.materials


def _check_three_point(res):
    lights = {o.name: o for o in bpy.data.objects if o.type == "LIGHT"}
    assert set(lights) == {"Key Light", "Fill Light", "Rim Light"}
    k, f = lights["Key Light"].data, lights["Fill Light"].data
    assert k.type == "AREA" and abs(f.energy - 0.4 * k.energy) < 1e-3 and k.energy > 0
    # key light points at the centre (local -Z toward origin)
    from mathutils import Vector
    lo = lights["Key Light"]
    fwd = lo.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    return fwd.dot((Vector(res["center"]) - lo.location).normalized()) > 0.99


def _check_hdri(res):
    w = bpy.context.scene.world
    types = {n.bl_idname for n in w.node_tree.nodes}
    env = next(n for n in w.node_tree.nodes if n.bl_idname == "ShaderNodeTexEnvironment")
    mp = next(n for n in w.node_tree.nodes if n.bl_idname == "ShaderNodeMapping")
    return ({"ShaderNodeLightPath", "ShaderNodeMixShader"} <= types and env.image is not None
            and env.image.size[0] > 0 and abs(mp.inputs["Rotation"].default_value[2] - 1.5708) < 1e-3
            and w.node_tree.nodes["World Output"].inputs["Surface"].is_linked)


def _check_sky(res):
    import math
    w = bpy.context.scene.world
    sky = next(n for n in w.node_tree.nodes if n.bl_idname == "ShaderNodeTexSky")
    sun = bpy.data.objects["Sky Sun"]
    from mathutils import Vector
    light_dir = sun.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    return (sky.sky_type == "MULTIPLE_SCATTERING" and abs(math.degrees(sky.sun_elevation) - 30) < 1e-3
            and light_dir.dot(-Vector(res["sun_direction"])) > 0.999)


def _check_sun(res):
    sun = bpy.data.objects["Sun"]
    # noon at equinox-ish, 45N: elevation about 45 + 23 = 68 deg in June, sun to the south (az 180)
    return (60 < res["elevation_deg"] < 75 and abs(res["azimuth_deg"] - 180) < 2 and sun.data.energy > 3
            and res["direction_to_sun"][1] < 0 and sun.data.type == "SUN")


def _check_catcher(res):
    o = bpy.data.objects["ShadowCatcher"]
    return o.is_shadow_catcher and bpy.context.scene.render.film_transparent and abs(res["z"] + 1.0) < 1e-3 \
        and abs(res["size"] - 4.0 * 3) < 1e-3


def _check_frame(res):
    from bpy_extras.object_utils import world_to_camera_view
    sc = bpy.context.scene
    cam = sc.camera
    assert cam and cam.name == "Camera"
    o = bpy.data.objects["FrameMe"]
    from mathutils import Vector
    pts = [world_to_camera_view(sc, cam, o.matrix_world @ Vector(c)) for c in o.bound_box]
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    inside = min(xs) >= 0 and max(xs) <= 1 and min(ys) >= 0 and max(ys) <= 1
    tight = max(max(xs) - min(xs), max(ys) - min(ys)) > 0.75
    return inside and tight


def _check_turntable(res):
    sc = bpy.context.scene
    piv = bpy.data.objects["TurntablePivot"]
    cam = bpy.data.objects["TurntableCam"]
    sc.frame_set(1)
    a0 = piv.rotation_euler.z
    sc.frame_set(25)  # quarter turn with linear keys over 100 frames (end key at 101)
    a1 = piv.rotation_euler.z
    sc.frame_set(1)
    return cam.parent == piv and sc.camera == cam and sc.frame_end == 100 and abs(a0) < 1e-4 \
        and abs(a1 - 2 * 3.14159265 * 24 / 100) < 1e-3


def _check_dof(res):
    cam = bpy.context.scene.camera
    return cam.data.dof.use_dof and cam.data.dof.focus_object.name == "DOFDemo" and abs(cam.data.dof.aperture_fstop - 1.8) < 1e-5


def _check_preset(res):
    sc = bpy.context.scene
    return (sc.render.engine == "CYCLES" and sc.cycles.samples == 256 and sc.cycles.use_denoising
            and sc.render.resolution_percentage == 100 and sc.view_settings.view_transform == "AgX"
            and sc.view_settings.look == "AgX - Punchy" and sc.render.film_transparent
            and sc.render.image_settings.color_mode == "RGBA")


def _check_render(res):
    sc = bpy.context.scene
    return (os.path.isfile(res["path"]) and res["size"] == [48, 32] and res["temp_camera"]
            and sc.camera is None and "BP_TempRenderCam" not in bpy.data.objects
            and sc.render.resolution_x == 1920)


def _check_comp(res):
    ng = bpy.context.scene.compositing_node_group
    types = {n.bl_idname for n in ng.nodes}
    glare = next(n for n in ng.nodes if n.bl_idname == "CompositorNodeGlare")
    out = next(n for n in ng.nodes if n.bl_idname == "NodeGroupOutput")
    return ({"CompositorNodeRLayers", "CompositorNodeLensdist", "CompositorNodeImageCoordinates"} <= types
            and glare.inputs["Type"].default_value == "Fog Glow" and out.inputs[0].is_linked
            and not res["invalid_links"] and res["chain"][-1] == "Vignette")


def _check_outline(res):
    o = bpy.data.objects["OutlineDemo"]
    mod = o.modifiers["Outline"]
    mats = [s.material.name for s in o.material_slots]
    return (mod.use_flip_normals and mod.offset == 1.0 and mats[mod.material_offset] == "Outline_Ink"
            and _mat("Outline_Ink").use_backface_culling and len(mats) == 2)


def _check_bake(res):
    sc = bpy.context.scene
    for k in ("AO", "DIFFUSE", "ROUGHNESS"):
        f = res["maps"][k]
        assert os.path.isfile(f["path"]) and f["size"] == [32, 32], f
    d = bpy.data.images["BakeDemo_diffuse"]
    px = list(d.pixels[:])
    assert max(px[0::4]) > 0.05, "diffuse bake is black"
    o = bpy.data.objects["BakeDemo"]
    assert [s.material.name for s in o.material_slots] == ["BakeDemo_Baked"]
    wood = _mat("BakeDemoWood")
    assert not any(n.bl_idname == "ShaderNodeTexImage" for n in wood.node_tree.nodes), "temp bake node left"
    return sc.render.engine != "CYCLES" or sc.cycles.samples != 2


def _check_normal_bake(res):
    img = bpy.data.images["NormalBakeLow_normal"]
    px = list(img.pixels[:])
    blues = px[2::4]
    reds = px[0::4]
    assert os.path.isfile(res["path"]) and res["size"] == [32, 32]
    assert sum(blues) / len(blues) > 0.6  # tangent-space normals are mostly blue
    assert max(reds) - min(reds) > 0.02  # but not flat: high-poly detail was transferred
    return _linked("NormalBakeLow_Mat", "ShaderNodeBsdfPrincipled", "Normal") and not bpy.data.objects["NormalBakeLow"].select_get()


def _setup_cube(name, size=2.0, z=0.0):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    o.location.z = z
    bpy.context.scene.collection.objects.link(o)
    return o


# Some cases need a named object in the freshly reset scene. The runner resets the scene, then expands **params;
# LazyParams builds the object at that moment (keys() is called by ** for dict subclasses overriding __iter__).
# Must be non-empty so the runner's `params or {}` keeps it.
class LazyParams(dict):
    def __init__(self, setup, **kw):
        super().__init__(**kw)
        self._setup = setup
        self._done = False

    def keys(self):
        if not self._done:
            self._done = True
            self._setup()
        return super().keys()

    def __iter__(self):
        return iter(self.keys())


CASES = [
    ("pbr_material_from_textures", {"folder": TEX, "name": "Brick", "uv_scale": 2.0}, _check_pbr),
    ("procedural_material", LazyParams(lambda: _setup_cube("Plank"), preset="wood", target="Plank"),
     _check_procedural),
    ("glass_material", {"name": "Pane", "thin_wall": True, "color": "#e0f0ff"}, _check_glass),
    ("emissive_neon_material", {"name": "NeonPink", "color": (1, 0.1, 0.6), "strength": 20},
     lambda r: r["material"] == "NeonPink" and _linked("NeonPink", "ShaderNodeEmission", "Color")
     and _mat("NeonPink").node_tree.nodes["Emission"].inputs["Strength"].default_value == 20),
    ("toon_shader_material", {"name": "Cel", "steps": 3},
     lambda r: {"ShaderNodeShaderToRGB", "ShaderNodeLayerWeight"} <= _types("Cel")
     and len(next(n for n in _mat("Cel").node_tree.nodes if n.bl_idname == "ShaderNodeValToRGB"
                  and n.color_ramp.interpolation == "CONSTANT" and len(n.color_ramp.elements) == 3).color_ramp.elements) == 3),
    ("triplanar_material", {"name": "Tri", "blend": 0.3},
     lambda r: any(n.bl_idname == "ShaderNodeTexImage" and n.projection == "BOX" and abs(n.projection_blend - 0.3) < 1e-6
                   and n.image.generated_type == "COLOR_GRID" for n in _mat("Tri").node_tree.nodes)),
    ("uv_checker_material", {"demo": True},
     lambda r: r["applied"] == ["UVCheckDemo"] and bpy.data.objects["UVCheckDemo"].active_material.name == "UV_Checker"
     and list(bpy.data.objects["UVCheckDemo"]["bp_uvcheck_prev"]) == []),
    ("random_color_per_object", {"demo": True, "palette": ["#ff0000", "#00ff00", "#0000ff"]},
     lambda r: len(r["assigned_to"]) == 5 and r["palette_size"] == 3
     and _linked("RandomPalette", "ShaderNodeValToRGB", "Fac")
     and all(bpy.data.objects[f"VariantDemo_{i}"].active_material.name == "RandomPalette" for i in range(5))),
    ("vertex_color_material", {"demo": True},
     lambda r: r["attribute"] == "Col" and r["assigned_to"] == ["VertexColorDemo"]
     and _mat("VertexColor").node_tree.nodes["Color Attribute"].layer_name == "Col"),
    ("replace_material", {"old": "Steel", "new": "Gold", "remove_old": True, "demo": True},
     lambda r: r["slots_changed"] == 2 and r["old_removed"] and "Steel" not in bpy.data.materials
     and all(bpy.data.objects[f"ReplaceDemo_{i}"].active_material.name == "Gold" for i in range(2))),
    ("material_report", {"demo": True, "purge_unused": True}, _check_report),
    ("three_point_lighting", LazyParams(lambda: _setup_cube("Subject"), distance_factor=2.5), _check_three_point),
    ("hdri_world", {"builtin": "forest", "strength": 0.8, "rotation_deg": 90, "camera_background": (0.05, 0.05, 0.05)},
     _check_hdri),
    ("sky_world", {"sun_elevation_deg": 30, "sun_rotation_deg": 45, "add_sun_light": True}, _check_sky),
    ("sun_time_of_day", {"hour": 12.0, "day_of_year": 172, "latitude_deg": 45}, _check_sun),
    ("shadow_catcher_ground", LazyParams(lambda: _setup_cube("Prop", 3.0, 0.5), size_factor=4.0), _check_catcher),
    ("frame_objects_camera", LazyParams(lambda: _setup_cube("FrameMe", 1.0, 2.0), lens=35, margin=0.05),
     _check_frame),
    ("turntable_setup", LazyParams(lambda: _setup_cube("Vase"), frames=100), _check_turntable),
    ("camera_dof_focus", {"demo": True, "fstop": 1.8},
     lambda r: _check_dof(r) and r["focus_distance"] > 0),
    ("set_render_preset", {"preset": "final", "engine": "CYCLES", "look": "AgX - Punchy", "film_transparent": True},
     _check_preset),
    ("render_still", LazyParams(lambda: _setup_cube("RenderMe"), filepath=os.path.join(TMP, "still.png"),
                                resolution=(48, 32), engine="CYCLES", samples=2), _check_render),
    ("compositor_effects", {"glare_type": "FOG_GLOW", "vignette": 0.4, "lens_distortion": -0.02, "dispersion": 0.01},
     _check_comp),
    ("outline_effect", {"method": "inverted_hull", "thickness": 0.03}, _check_outline),
    ("bake_maps", {"maps": ["AO", "BASECOLOR", "ROUGHNESS"], "resolution": 32, "samples": 2, "output_dir": TMP,
                   "apply_material": True}, _check_bake),
    ("bake_normal_high_to_low", {"resolution": 32, "output_path": os.path.join(TMP, "n.png")}, _check_normal_bake),
]
