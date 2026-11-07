"""BlendPro MCP server: token-efficient Blender control for LLM agents."""
import glob
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP, Image

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("BlendPro")

GUI_PORT = int(os.getenv("BLENDPRO_PORT", "9877"))
HEADLESS_PORT = int(os.getenv("BLENDPRO_HEADLESS_PORT", "9878"))
HOST = os.getenv("BLENDPRO_HOST", "127.0.0.1")
TIMEOUT = float(os.getenv("BLENDPRO_TIMEOUT", "600"))
ADDON_DIR = Path(__file__).parent / "addon"

INSTRUCTIONS = """BlendPro controls Blender. Work text-first; images are the last resort.
Loop: scene_overview → (inspect / node_tree_get / mesh_analysis as needed) → edit → read the returned `diff` → verify with
mesh_analysis / spatial_report → render_views ONCE at the end (or when judging looks).
- run_python returns stdout, a `result` variable you assign, full traceback with the failing line, and a scene diff — no need to re-query.
- Prefer node_tree_build (one call builds/patches a whole material or Geometry Nodes tree) over node-by-node Python.
- run_operators handles mode/selection/viewport context for bpy.ops (edit-mode ops, add-on operators).
- Unsure of an API name/socket/enum? api_lookup first (it reads the installed Blender version) instead of guessing.
- checkpoint(save) before risky/destructive steps; checkpoint(restore) to roll back.
- No Blender window open? blender_session(action='start_headless', blend_file=...) runs a background Blender.
- Need an organic/complex asset (character, prop, creature)? tripo_generate (AI text/image→3D, costs Tripo credits;
  start with preset='draft' or 'xr_lowpoly' to iterate cheaply), then tripo_process for retopo/rig/animation.
  Models the user sends from Tripo Studio arrive via the Tripo Bridge add-on - check with scene_overview.
Rotations in edit_objects are degrees; Blender units are metres; Z is up; front view looks along +Y."""

mcp = FastMCP("blendpro", instructions=INSTRUCTIONS)


# ---------------------------------------------------------------- connection

class Conn:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.sock = None
        self.file = None
        self.lock = threading.Lock()
        self.seq = 0

    def _connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.sock.settimeout(TIMEOUT)
        self.file = self.sock.makefile("rb")

    def close(self):
        for x in (self.file, self.sock):
            try:
                x and x.close()
            except Exception:
                pass
        self.sock = self.file = None

    def send(self, ctype, params=None, retry=True):
        line = None
        err = None
        with self.lock:
            try:
                if self.sock is None:
                    self._connect()
                self.seq += 1
                msg = json.dumps({"id": self.seq, "type": ctype, "params": params or {}}).encode() + b"\n"
                self.sock.sendall(msg)
                line = self.file.readline()
                if not line:
                    raise ConnectionError("connection closed by Blender")
            except (ConnectionError, OSError) as e:
                self.close()
                err = e
        if err is not None:
            # a stale socket (Blender restarted) is retried once; a timeout is not (Blender may still be working)
            if retry and not isinstance(err, socket.timeout):
                return self.send(ctype, params, retry=False)
            raise ConnectionError(_conn_hint(self.port, err))
        resp = json.loads(line)
        if resp.get("status") == "error":
            msg = resp.get("message", "unknown error")
            if resp.get("traceback"):
                msg += "\n" + resp["traceback"]
            raise RuntimeError(msg)
        return resp.get("result")


def _conn_hint(port, e):
    return (f"Cannot reach Blender on port {port} ({e}). Open Blender with the 'BlendPro MCP' add-on enabled "
            f"(sidebar > BlendPro > Start), or call blender_session(action='start_headless').")


_state: dict[str, Any] = {"mode": "gui", "proc": None, "conns": {}}


def connection() -> Conn:
    port = HEADLESS_PORT if _state["mode"] == "headless" else GUI_PORT
    c = _state["conns"].get(port)
    if c is None:
        c = _state["conns"][port] = Conn(HOST, port)
    return c


def call(ctype, **params):
    return connection().send(ctype, {k: v for k, v in params.items() if v is not None})


def out(obj) -> str:
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)


# ---------------------------------------------------------------- session

def _find_blender():
    exe = os.getenv("BLENDER_EXE")
    if exe and os.path.exists(exe):
        return exe
    cands = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"), reverse=True)
    cands += ["/Applications/Blender.app/Contents/MacOS/Blender", "/usr/bin/blender", "/snap/bin/blender"]
    for c in cands:
        if os.path.exists(c):
            return c
    from shutil import which
    return which("blender")


@mcp.tool()
def blender_session(action: str = "status", blend_file: Optional[str] = None) -> str:
    """Connection control. action: status | start_headless (spawn background Blender, optionally opening blend_file;
    all tools then target it) | stop_headless | use_gui (target the open Blender window, port 9877)."""
    if action == "status":
        res = {"target": _state["mode"]}
        for mode, port in (("gui", GUI_PORT), ("headless", HEADLESS_PORT)):
            try:
                c = _state["conns"].get(port) or Conn(HOST, port)
                _state["conns"][port] = c
                res[mode] = c.send("ping")
            except Exception as e:
                res[mode] = f"unreachable ({type(e).__name__})"
        return out(res)
    if action == "use_gui":
        _state["mode"] = "gui"
        return out({"target": "gui", "ping": call("ping")})
    if action == "start_headless":
        if _state["proc"] and _state["proc"].poll() is None:
            _state["mode"] = "headless"
            if blend_file:
                call("open_file", path=blend_file)
            return out({"target": "headless", "note": "already running", "ping": call("ping")})
        exe = _find_blender()
        if not exe:
            return "Blender executable not found; set BLENDER_EXE."
        cmd = [exe, "--background"]
        if blend_file:
            cmd.append(os.path.abspath(blend_file))
        cmd += ["--python", str(ADDON_DIR / "headless_runner.py"), "--", "--port", str(HEADLESS_PORT)]
        logf = open(os.path.join(os.getenv("TEMP", "/tmp"), "blendpro_headless.log"), "w")
        _state["proc"] = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _state["mode"] = "headless"
        _state["conns"].pop(HEADLESS_PORT, None)
        deadline = time.time() + 90
        while time.time() < deadline:
            if _state["proc"].poll() is not None:
                return f"Headless Blender exited early (code {_state['proc'].returncode}); see {logf.name}"
            try:
                return out({"target": "headless", "blender": exe, "ping": call("ping")})
            except Exception:
                _state["conns"].pop(HEADLESS_PORT, None)
                time.sleep(0.5)
        return "Timed out waiting for headless Blender"
    if action == "stop_headless":
        try:
            connection_for(HEADLESS_PORT).send("shutdown", retry=False)
        except Exception:
            pass
        p = _state["proc"]
        if p and p.poll() is None:
            try:
                p.wait(10)
            except Exception:
                p.kill()
        _state["proc"] = None
        _state["conns"].pop(HEADLESS_PORT, None)
        _state["mode"] = "gui"
        return "headless Blender stopped; target is gui"
    return "action must be status | start_headless | stop_headless | use_gui"


def connection_for(port):
    c = _state["conns"].get(port)
    if c is None:
        c = _state["conns"][port] = Conn(HOST, port)
    return c


# ---------------------------------------------------------------- understanding

@mcp.tool()
def scene_overview(filter: Optional[str] = None, types: Optional[list[str]] = None,
                   collection: Optional[str] = None, limit: int = 300) -> str:
    """Whole scene as compact text: file/render/units/camera header, then the collection + parent hierarchy with one
    line per object (transform, dims, vert/face counts incl. after-modifier counts, modifiers with key settings,
    materials, flags). filter = name glob ('*wheel*'), types = ['MESH','LIGHT'...]. Start here."""
    return call("scene_overview", filter=filter, types=types, collection=collection, limit=limit)


@mcp.tool()
def inspect(names: list[str], sections: Optional[list[str]] = None) -> str:
    """Deep details for objects. sections (default all): transform, data (mesh counts/UVs/attributes/vertex groups/
    shape keys/evaluated counts; light/camera/curve/armature data), modifiers (non-default settings, GN inputs),
    materials (Principled values, images), constraints, animation (action, fcurves, drivers), custom."""
    return out(call("inspect", names=names, sections=sections))


@mcp.tool()
def mesh_analysis(names: list[str], evaluated: bool = True, target: Optional[str] = None,
                  self_intersections: bool = True, details: bool = False) -> str:
    """Topology/health report per mesh with an `issues` list and fix hints: tris/quads/ngons, boundary/non-manifold/
    wire edges, inconsistent normals, inside-out, duplicates, degenerate faces, poles, self-intersections, X symmetry,
    UV islands/zero-area/outside 0-1, material usage, unapplied scale. evaluated=True includes modifiers.
    target = quest|webxr|mobile|unity|film adds budget checks (tris, materials, texture size)."""
    return out(call("mesh_analysis", names=names, evaluated=evaluated, target=target,
                    self_intersections=self_intersections, details=details))


@mcp.tool()
def spatial_report(names: Optional[list[str]] = None, ground_z: float = 0.0, ascii_map: bool = True) -> str:
    """Layout check without images: per-object world bounds and support (on ground / on X / floating by d / sunk),
    bbox overlaps with true surface-intersection test, camera framing (screen coverage, cropped, out of frame), and
    ASCII top (XY) and front (XZ) maps with a legend. names = subset (default all visible)."""
    return out(call("spatial_report", names=names, ground_z=ground_z, ascii_map=ascii_map))


@mcp.tool()
def node_tree_get(target: str, include_defaults: bool = False) -> str:
    """Read a node tree as compact JSON: nodes {name,type,loc,props(non-default),inputs(non-default unlinked)},
    links [from,from_socket,to,to_socket], group interface. target: material:<Mat> | object:<Obj>/material |
    object:<Obj>/geonodes[:Modifier] | group:<NodeGroup> | world | compositor."""
    return out(call("node_tree_get", target=target, include_defaults=include_defaults))


@mcp.tool()
def node_tree_build(target: str, nodes: Optional[list[dict]] = None, links: Optional[list] = None,
                    remove: Optional[list[str]] = None, unlink: Optional[list[list]] = None, clear: bool = False,
                    interface: Optional[list[dict]] = None) -> str:
    """Create or patch a whole node tree in one call (missing material / GN modifier+group are created).
    nodes: [{name, type?(bl_idname, needed for new nodes), props?{data_type/operation/blend_type/image/color_ramp:
    {elements:[[pos,[r,g,b,a]]]}...}, inputs?{socket name or index: value}, loc?, label?}] — existing names are updated.
    Props are applied before inputs. Colors: [r,g,b(,a)] linear or '#hex' (sRGB). Object/material/image sockets take names.
    links: [[from, from_socket, to, to_socket]] or "From.Socket -> To.Socket"; duplicate socket names → use index.
    target: material:<Mat> | object:<Obj>/material | object:<Obj>/geonodes[:Mod] | geometry_group:<N> |
    shader_group:<N> | world | compositor. interface (groups/GN): [{name,in_out:INPUT|OUTPUT,socket_type,default_value}].
    Returns created/updated/errors (with valid socket/prop names) and, for geonodes, evaluated vert/face/instance counts."""
    return out(call("node_tree_build", target=target, nodes=nodes, links=links, remove=remove, unlink=unlink,
                    clear=clear, interface=interface))


@mcp.tool()
def api_lookup(query: str, limit: int = 40) -> str:
    """Introspect the installed Blender's API instead of guessing. query forms:
    op:mesh.bevel (args, types, defaults, enum items) | node:Principled or node:GeometryNodeSetPosition (sockets with
    indices/defaults, props+enums) | struct:SubsurfModifier (properties, functions) | path:C.scene.eevee (live values) |
    enum:Object.type | search:<text> (ops, nodes, structs matching)."""
    return out(call("api_lookup", query=query, limit=limit))


# ---------------------------------------------------------------- editing

@mcp.tool()
def run_python(code: str, diff: bool = True) -> str:
    """Execute bpy code in Blender. Preloaded: bpy, bmesh, mathutils, math, Vector, Matrix, Euler, C (context),
    D (data), obj(name) (lookup with did-you-mean), radians, degrees, persist (dict kept between calls).
    Assign `result = ...` to get JSON back. Returns ok, stdout, result, error+traceback with the failing line and
    hints, and `diff` (objects added/removed/changed, datablock counts, mode). Batch many edits in one call."""
    return out(call("run_python", code=code, diff=diff))


@mcp.tool()
def run_operators(steps: list[dict], stop_on_error: bool = True) -> str:
    """Run bpy.ops with context handled. Each step: {op:'mesh.bevel', args:{...}, object|objects (made active/
    selected), mode:'EDIT'|'OBJECT'|'SCULPT'|..., select_all:true|false (edit mode), select_mode:'VERT'|'EDGE'|'FACE',
    then_mode}. Checks poll() and explains failures. Works for add-on operators (LoopTools, Bool Tool, ...).
    Returns per-step results + scene diff."""
    return out(call("run_operators", steps=steps, stop_on_error=stop_on_error))


@mcp.tool()
def edit_objects(operations: list[dict]) -> str:
    """Batch common edits in one round trip; each item {op, name, ...}:
    add{primitive:CUBE|UV_SPHERE|ICO_SPHERE|CYLINDER|CONE|PLANE|TORUS|CIRCLE|MONKEY|GRID|EMPTY, name, location,
    rotation(deg), scale, dimensions, args{vertices,radius,...}, collection} | transform{location,rotation,scale,dimensions}
    | delete | duplicate{new_name, offset, linked} | rename{new_name} | material{material, color, metallic, roughness,
    inputs{Principled socket:value}, slot} | modifier{type, mod_name, props{}, index} | remove_modifier | apply_modifier
    | apply_transform{location,rotation,scale} | parent{parent, keep_transform} | visibility{hide, hide_render}
    | shade{smooth, auto_smooth_angle} | origin{type, center}. Returns per-op results + scene diff."""
    return out(call("edit_objects", operations=operations))


@mcp.tool()
def checkpoint(action: str = "save", name: Optional[str] = None) -> str:
    """Snapshot the scene to a temp .blend (save), roll back to it (restore), or list. Use before destructive edits."""
    return out(call("checkpoint", action=action, name=name))


@mcp.tool()
def file_ops(action: str, path: Optional[str] = None, copy: bool = False) -> str:
    """action: save (to path, or back to the original file after a checkpoint restore; copy=True keeps working file)
    | open (load a .blend)."""
    if action == "save":
        return out(call("save_file", path=path, copy=copy))
    if action == "open":
        return out(call("open_file", path=path))
    return "action must be save or open"


# ---------------------------------------------------------------- visuals

@mcp.tool()
def render_views(views: Optional[list[str]] = None, size: int = 320, objects: Optional[list[str]] = None,
                 engine: str = "workbench", color: str = "material", wireframe: bool = False):
    """ONE contact-sheet image of several auto-framed views (works headless). views from: front, back, left, right,
    top, bottom, iso, iso_back, camera (default front,right,top,iso). size = px per view (keep small: 256-400).
    objects = isolate these. engine: workbench (fast, solid+cavity+outline) | eevee | cycles.
    color (workbench): material|object|random|single|texture|vertex. wireframe overlays edges to judge topology."""
    res = call("render_views", views=views or ["front", "right", "top", "iso"], size=size, objects=objects,
               engine=engine, color=color, wireframe=wireframe)
    return [Image(path=res["filepath"]), out({k: res[k] for k in ("layout", "engine", "bounds")})]


@mcp.tool()
def viewport_screenshot(max_size: int = 640):
    """Screenshot of the live 3D viewport (GUI only) — shows exactly what the user sees incl. overlays/selection."""
    res = call("viewport_screenshot", max_size=max_size)
    return Image(path=res["filepath"])


# ---------------------------------------------------------------- pipeline

@mcp.tool()
def export_asset(filepath: str, objects: Optional[list[str]] = None, preset: str = "glb",
                 options: Optional[dict] = None, check: bool = True) -> str:
    """Export with tested presets: unity_fbx (axis -Z fwd/Y up, FBX_SCALE_ALL, no leaf bones, apply modifiers) |
    glb | webxr_glb (Draco) | obj | usd. objects = selection to export (default all). options override exporter args.
    check=True runs mesh_analysis budget checks first (quest budget for webxr, unity budget for unity_fbx)."""
    return out(call("export_asset", filepath=filepath, objects=objects, preset=preset, options=options, check=check))


@mcp.tool()
def addons(action: str = "list", module: Optional[str] = None, filepath: Optional[str] = None) -> str:
    """Manage add-ons: list | enable/disable {module} | operators {module} (bpy.ops idnames it provides) |
    install {filepath: .zip/.py} (extension zips go to user_default repo). Extension modules are named bl_ext.<repo>.<id>."""
    return out(call("addons", action=action, module=module, filepath=filepath))


# ---------------------------------------------------------------- Tripo3D

def _tripo_finish(task, import_to_blender, name, location, target_size, preview, extra_props=None):
    from . import tripo
    res = {"task": tripo.summarize(task)}
    if task.get("status") != "success":
        if task.get("status") not in tripo.TERMINAL:
            res["note"] = f"still {task.get('status')} ({task.get('progress')}%). Call tripo_task(action='wait', task_id=...)"
        return res, None
    path = tripo.download(task)
    res["file"] = path
    if import_to_blender:
        props = {"tripo_task_id": task["task_id"], "tripo_type": task.get("type", "")}
        props.update(extra_props or {})
        res["imported"] = call("import_model", filepath=path, name=name or f"tripo_{task['task_id'][:8]}",
                               location=location or [0, 0, 0], target_size=target_size, custom_props=props)
    img = None
    if preview and (task.get("output") or {}).get("rendered_image_url"):
        try:
            import httpx
            img = Image(data=httpx.get(task["output"]["rendered_image_url"], timeout=60).content, format="png")
        except Exception as e:
            res["preview_error"] = str(e)
    return res, img


def _tripo_input_from(value: str) -> str:
    """Blender object name -> its Tripo task id (if imported from Tripo) or an exported+uploaded GLB."""
    from . import tripo
    if not value:
        raise ValueError("input required")
    if os.path.exists(value) or value.startswith(("http://", "https://", "file_")) or (len(value) == 36 and value.count("-") == 4):
        return tripo.resolve_input(value)
    # treat as Blender object name
    info = call("run_python", code=f"o=obj({value!r}); root=o\nwhile root.parent: root=root.parent\n"
                                   f"result={{'task': root.get('tripo_task_id'), 'edited': bool(root.get('tripo_edited'))}}", diff=False)
    if not info.get("ok"):
        raise ValueError(f"input {value!r} is not a file, URL, file token, task id or Blender object: {info.get('error')}")
    meta = info.get("result") or {}
    if meta.get("task") and not meta.get("edited"):
        return meta["task"]
    path = str(tripo.DOWNLOAD_DIR / f"upload_{int(time.time())}.glb")
    tripo.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    names = call("run_python", code=f"o=obj({value!r}); result=[o.name]+[c.name for c in o.children_recursive]", diff=False)["result"]
    call("export_asset", filepath=path, objects=names, preset="glb", check=False)
    return tripo.upload(path)


@mcp.tool()
def tripo_generate(prompt: Optional[str] = None, image: Optional[str] = None, views: Optional[dict] = None,
                   preset: str = "standard", options: Optional[dict] = None, negative_prompt: Optional[str] = None,
                   wait: bool = True, import_to_blender: bool = True, name: Optional[str] = None,
                   location: Optional[list[float]] = None, target_size: Optional[float] = None, preview: bool = False):
    """Generate a 3D model with Tripo3D (costs Tripo API credits) and import it into Blender, grounded at `location`
    and scaled so its largest side = target_size metres. One of: prompt (text→3D) | image (local path/URL/file token
    → image→3D) | views {front,left,back,right: path/URL} (multiview, front + ≥1 more).
    preset: draft (no texture, cheapest) | standard | detailed | game_ready (smart low-poly 10k) | xr_lowpoly (5k) |
    quad. options: raw API fields override (model, face_limit, pbr, texture_quality, geometry_quality, quad,
    smart_low_poly, generate_parts, model_seed, texture_seed...). wait polls up to ~4 min; otherwise use tripo_task.
    preview=True also returns Tripo's render (costs image tokens). Follow with mesh_analysis(target=...)."""
    from . import tripo
    body = {"model": tripo.DEFAULT_MODEL}
    body.update(tripo.PRESETS.get(preset, {}))
    if negative_prompt:
        body["negative_prompt"] = negative_prompt
    if views:
        body["inputs"] = [{k: tripo.resolve_input(v)} for k, v in views.items() if v]
        path = "/generation/multiview-to-model"
    elif image:
        body["input"] = tripo.resolve_input(image)
        path = "/generation/image-to-model"
    elif prompt:
        body["prompt"] = prompt
        path = "/generation/text-to-model"
    else:
        return "give prompt, image or views"
    body.update(options or {})
    if body.get("generate_parts"):
        body.update(texture=False, pbr=False)
    task_id = tripo.create(path, body)
    if not wait:
        return out({"task_id": task_id, "submitted": body})
    task = tripo.wait(task_id)
    res, img = _tripo_finish(task, import_to_blender, name, location, target_size, preview,
                             {"tripo_prompt": (prompt or "")[:200]})
    return [out(res), img] if img else out(res)


@mcp.tool()
def tripo_process(operation: str, input: str, params: Optional[dict] = None, wait: bool = True,
                  import_to_blender: bool = True, name: Optional[str] = None,
                  location: Optional[list[float]] = None, target_size: Optional[float] = None):
    """Tripo post-processing (costs credits). input: Tripo task_id | Blender object name (reuses its tripo task id if
    unedited, else exports+uploads it as GLB) | local file | URL. operation + common params:
    retopo {model:v3.0|v2.0|v1.0, face_limit 500-20000, quad, smart_uv(v3.0), bake} |
    texture {texture_prompt:{text}|{image}, texture_quality, pbr, model} |
    convert {format:GLTF|FBX|USDZ|OBJ|STL|3MF (required), face_limit, quad, texture_size, pivot_to_center_bottom,
    fbx_preset, export_orientation} | rig_check | rig {rig_type:biped|quadruped|..., spec:tripo|mixamo|unity|unreal|vrm,
    model:v3.0-20260909 (humanoid), out_format} | retarget {animation:'preset:walk' or animations:[..≤5], animate_in_place}
    (input = rig task id) | segment | complete | uv. Mark a Tripo object as hand-edited with custom prop tripo_edited=1
    to force re-upload."""
    from . import tripo
    if operation not in tripo.PROCESS:
        return f"operation must be one of {sorted(tripo.PROCESS)}"
    body = {"input": _tripo_input_from(input)}
    body.update(params or {})
    if operation == "convert" and "format" not in body:
        body["format"] = "GLTF"
    task_id = tripo.create(tripo.PROCESS[operation], body)
    if not wait:
        return out({"task_id": task_id, "submitted": {k: v for k, v in body.items() if k != "input"}})
    task = tripo.wait(task_id, timeout=300)
    do_import = import_to_blender and operation not in ("rig_check",)
    res, _ = _tripo_finish(task, do_import, name or f"{operation}_{task_id[:8]}", location, target_size, False)
    if operation == "rig_check":
        res["output"] = task.get("output")
    return out(res)


@mcp.tool()
def tripo_task(action: str = "status", task_id: Optional[str] = None, import_to_blender: bool = True,
               name: Optional[str] = None, location: Optional[list[float]] = None, target_size: Optional[float] = None):
    """action: status {task_id} | wait {task_id} (poll ~4 min, then import if done) | import {task_id} |
    balance (API credits) | bridge (state of Tripo's own Bridge add-on: Tripo Studio pushes models into Blender over
    ws://127.0.0.1:60600; imported objects then show up in scene_overview)."""
    from . import tripo
    if action == "balance":
        return out(tripo.balance())
    if action == "bridge":
        return out(call("tripo_bridge_status"))
    if not task_id:
        return "task_id required"
    if action == "status":
        return out(tripo.summarize(tripo.get_task(task_id)))
    if action in ("wait", "import"):
        task = tripo.wait(task_id) if action == "wait" else tripo.get_task(task_id)
        res, _ = _tripo_finish(task, import_to_blender, name, location, target_size, False)
        return out(res)
    return "action must be status | wait | import | balance | bridge"


# ---------------------------------------------------------------- recipes

@mcp.tool()
def recipes(action: str = "list", name: Optional[str] = None, params: Optional[dict] = None,
            category: Optional[str] = None, query: Optional[str] = None) -> str:
    """100+ tested one-call Blender recipes (modeling, uv, shading, lighting, camera, baking, rigging, animation,
    geometry nodes, XR/Unity pipeline). action: list (filter by category or query text) | doc {name} (signature +
    notes) | run {name, params}. Prefer a recipe over hand-written code when one fits; returns result + scene diff."""
    return out(call("recipes", action=action, name=name, params=params, category=category, query=query))


# ---------------------------------------------------------------- pose (MediaPipe)

@mcp.tool()
def pose_analyze(objects: list[str], view: str = "front", frames: Optional[list[int]] = None, size: int = 768,
                 engine: str = "workbench", create: str = "lines", min_visibility: float = 0.5,
                 model: str = "heavy", return_image: bool = True, rest_pose: bool = False,
                 armature_naming: str = "blender"):
    """Understand a humanoid character's pose: renders an ortho view (front|back|left|right), runs MediaPipe Pose
    (33 landmarks), draws the skeleton over the render, and lifts each joint into 3D at the middle of the limb
    (ray entry/exit through the mesh). Returns 3D joints (+ pelvis/spine/chest/neck/head_top), per-joint quality and
    metrics: elbow/knee angles, arm elevation (T-pose/A-pose), limb lengths + L/R asymmetry, spine lean, facing,
    ankle heights. create: lines (orange skeleton drawn in the viewport, in front of the mesh) | empties | both | none |
    armature (lines + a starter humanoid armature from the joints; armature_naming: blender|mixamo|unity).
    frames=[...] analyses an animation per frame (no images, compact metrics table) to check poses/foot contact.
    rest_pose=True analyses the armature rest pose (T/A-pose): use it for rigging; MediaPipe is much more accurate
    there than on twisted/dance frames. Character should face -Y (Blender front). Joints feed armature_from_joint_positions."""
    from . import posedetect

    def render_detect(frame=None):
        rend = det = None
        for eng in [engine] + (["eevee"] if engine != "eevee" else []):  # retry lit render if flat shading fails
            rend = call("pose_render", objects=objects, view=view, size=size, engine=eng, frame=frame, rest_pose=rest_pose)
            det = posedetect.detect(rend["filepath"], model)
            if det is not None:
                det["landmarks"], det["lr_swapped"] = posedetect.normalize_sides(det["landmarks"], view)
            if det is not None:
                break
        return rend, det

    if frames:
        rows = {}
        for f in frames:
            rend, det = render_detect(f)
            if det is None:
                rows[f] = "no pose detected"
                continue
            lift = call("pose_lift", landmarks=det["landmarks"], camera=rend["camera"], objects=objects,
                        create="none", min_visibility=min_visibility, frame=f)
            keep = {k: lift["joints"][k] for k in ("pelvis", "left_wrist", "right_wrist", "left_ankle", "right_ankle", "head_top")
                    if k in lift["joints"]}
            rows[f] = {"joints": keep, "metrics": {k: v for k, v in lift["metrics"].items() if "angle" in k or "height" in k or k in ("arm_pose", "spine_lean_deg")}}
        return out({"view": view, "frames": rows})
    rend, det = render_detect()
    if det is None:
        res = {"detected": False, "render": rend["filepath"],
               "hint": "No person found (tried workbench + eevee). MediaPipe's detector keys on a face/head: faceless "
                       "or mask-like heads, non-humanoids and very stylized proportions fail. Check the character faces "
                       "the view (front = facing -Y); try view='back'/'left', another frame, or a larger size."}
        return [out(res), Image(path=rend["filepath"])] if return_image else out(res)
    lift = call("pose_lift", landmarks=det["landmarks"], camera=rend["camera"], objects=objects,
                create="lines" if create == "armature" else create, rest_pose=rest_pose, min_visibility=min_visibility)
    lift["detected"] = True
    if create == "armature":
        joints = {k: v for k, v in lift["joints"].items() if not lift["quality"].get(k, "").startswith("low")}
        try:
            lift["armature"] = call("recipes", action="run", name="armature_from_joint_positions",
                                    params={"joints": joints, "preset": "mediapipe", "coords": "blender",
                                            "naming": armature_naming, "name": f"{objects[0]}_PoseRig"})["result"]
        except Exception as e:
            lift["armature_error"] = str(e)[:500]
    if det.get("lr_swapped"):
        lift["note"] = "MediaPipe mirrored left/right; labels were corrected using the known camera side"
    lift["view"] = view
    if not return_image:
        return out(lift)
    overlay = rend["filepath"].replace(".png", "_overlay.png")
    posedetect.draw(rend["filepath"], det["landmarks"], overlay, min_visibility)
    return [out(lift), Image(path=overlay)]


# ---------------------------------------------------------------- optional asset tools

if os.getenv("BLENDPRO_ASSET_TOOLS", "0") == "1":
    from . import assets as _assets
    _assets.register(mcp)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "install-addon":
        from .install import main as install_main
        sys.exit(install_main(sys.argv[2:]))
    mcp.run()


if __name__ == "__main__":
    main()
