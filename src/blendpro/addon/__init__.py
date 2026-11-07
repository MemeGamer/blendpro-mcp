bl_info = {
    "name": "BlendPro MCP",
    "author": "Varun VP (built on blendmcp by Owen Kent / blender-mcp by Siddharth Ahuja)",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > BlendPro",
    "description": "Token-efficient MCP bridge: scene diffs, node JSON, mesh/spatial analysis, multi-view renders",
    "category": "Interface",
}

import json
import os
import queue
import socket
import tempfile
import threading
import time
import traceback

import bpy

from . import scene as _scene
from . import nodes as _nodes
from . import analysis as _analysis
from . import render as _render
from . import ops as _ops
from . import imports as _imports
from . import pose as _pose
from . import recipes as _recipes

DEFAULT_PORT = 9877
VERSION = ".".join(map(str, bl_info["version"]))

_legacy = None


def _legacy_server():
    """blendmcp's handler object (PolyHaven/Sketchfab/Hyper3D/Hunyuan), loaded lazily."""
    global _legacy
    if _legacy is None:
        from . import legacy_blendmcp
        _legacy = legacy_blendmcp.BlendMCPServer()
    return _legacy


def _ping():
    return {"addon_version": VERSION, "blender": bpy.app.version_string, "file": bpy.data.filepath,
            "dirty": bpy.data.is_dirty, "background": bpy.app.background, "scene": bpy.context.scene.name,
            "mode": bpy.context.mode, "objects": len(bpy.context.scene.objects)}


def _screenshot(max_size=640):
    path = os.path.join(tempfile.gettempdir(), f"blendpro_shot_{int(time.time() * 1000)}.png")
    return _render.viewport_screenshot(path, max_size)


HANDLERS = {
    "ping": _ping,
    "scene_overview": _scene.scene_overview,
    "inspect": _scene.inspect,
    "node_tree_get": _nodes.get_tree,
    "node_tree_build": _nodes.build_tree,
    "mesh_analysis": _analysis.mesh_analysis,
    "spatial_report": _analysis.spatial_report,
    "render_views": _render.render_views,
    "viewport_screenshot": _screenshot,
    "run_python": _ops.run_python,
    "run_operators": _ops.run_operators,
    "edit_objects": _ops.edit_objects,
    "checkpoint": _ops.checkpoint,
    "save_file": _ops.save_file,
    "open_file": _ops.open_file,
    "api_lookup": _ops.api_lookup,
    "addons": _ops.addons,
    "export_asset": _ops.export_asset,
    "import_model": _imports.import_model,
    "tripo_bridge_status": _imports.tripo_bridge_status,
    "pose_render": _pose.pose_render,
    "pose_lift": _pose.pose_lift,
    "recipes": _recipes.recipes,
}


def dispatch(cmd):
    ctype = cmd.get("type")
    params = cmd.get("params") or {}
    h = HANDLERS.get(ctype)
    try:
        if h is not None:
            return {"status": "success", "result": h(**params)}
        if ctype and ctype.startswith("legacy."):
            resp = _legacy_server()._execute_command_internal({"type": ctype[7:], "params": params})
            return resp
        return {"status": "error", "message": f"Unknown command {ctype!r}. Known: {sorted(HANDLERS)}"}
    except TypeError as e:
        import inspect
        sig = ""
        try:
            sig = str(inspect.signature(h))
        except Exception:
            pass
        return {"status": "error", "message": f"{type(e).__name__}: {e}. Signature: {ctype}{sig}"}
    except Exception as e:
        tb = traceback.format_exc()
        return {"status": "error", "message": f"{type(e).__name__}: {e}", "traceback": tb[-2500:]}


# ---------------------------------------------------------------- socket server

class Server:
    """Newline-delimited JSON over TCP (localhost). GUI: work queued to a main-thread timer.
    Headless (blender --background): serve_blocking() runs commands directly on the main thread."""

    def __init__(self, host="127.0.0.1", port=DEFAULT_PORT):
        self.host, self.port = host, port
        self.sock = None
        self.running = False
        self.jobs = queue.Queue()

    # --- GUI mode
    def start(self):
        if self.running:
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(4)
        self.sock.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        if not bpy.app.timers.is_registered(_drain):
            bpy.app.timers.register(_drain, first_interval=0.05, persistent=True)
        print(f"[BlendPro] listening on {self.host}:{self.port}")

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        print("[BlendPro] stopped")

    def _accept_loop(self):
        while self.running:
            try:
                client, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._client_loop, args=(client,), daemon=True).start()

    def _client_loop(self, client):
        f = client.makefile("rb")
        try:
            for line in f:
                if not line.strip():
                    continue
                try:
                    cmd = json.loads(line)
                except Exception as e:
                    _send(client, {"status": "error", "message": f"bad json: {e}"})
                    continue
                done = threading.Event()
                box = {}
                self.jobs.put((cmd, box, done))
                done.wait()
                resp = box["resp"]
                if "id" in cmd:
                    resp["id"] = cmd["id"]
                _send(client, resp)
        except Exception as e:
            print("[BlendPro] client error:", e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    # --- headless mode
    def serve_blocking(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(4)
        self.running = True
        print(f"[BlendPro] headless listening on {self.host}:{self.port}", flush=True)
        while self.running:
            client, _ = self.sock.accept()
            f = client.makefile("rb")
            try:
                for line in f:
                    if not line.strip():
                        continue
                    cmd = json.loads(line)
                    if cmd.get("type") == "shutdown":
                        _send(client, {"status": "success", "result": "bye", "id": cmd.get("id")})
                        self.running = False
                        break
                    resp = dispatch(cmd)
                    if "id" in cmd:
                        resp["id"] = cmd["id"]
                    _send(client, resp)
            except (ConnectionError, OSError) as e:
                print("[BlendPro] client dropped:", e, flush=True)
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        self.sock.close()


def _send(client, obj):
    data = json.dumps(obj, default=str, separators=(",", ":")).encode("utf-8") + b"\n"
    client.sendall(data)


def _drain():
    srv = getattr(bpy.types, "blendpro_server", None)
    if srv is None:
        return 0.25
    try:
        while True:
            cmd, box, done = srv.jobs.get_nowait()
            try:
                box["resp"] = dispatch(cmd)
            except Exception as e:
                box["resp"] = {"status": "error", "message": str(e)}
            done.set()
    except queue.Empty:
        pass
    return 0.05


def start_server(port=None):
    srv = getattr(bpy.types, "blendpro_server", None)
    if srv and srv.running:
        return srv
    srv = Server(port=port or _prefs_port())
    srv.start()
    bpy.types.blendpro_server = srv
    return srv


def stop_server():
    srv = getattr(bpy.types, "blendpro_server", None)
    if srv:
        srv.stop()
    bpy.types.blendpro_server = None


# ---------------------------------------------------------------- UI

def _prefs():
    a = bpy.context.preferences.addons.get(__package__)
    return a.preferences if a else None


def _prefs_port():
    p = _prefs()
    return p.port if p else DEFAULT_PORT


class BLENDPRO_AP_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    port: bpy.props.IntProperty(name="Port", default=DEFAULT_PORT, min=1024, max=65535)
    autostart: bpy.props.BoolProperty(name="Start server when Blender opens", default=True)

    def draw(self, context):
        self.layout.prop(self, "port")
        self.layout.prop(self, "autostart")


class BLENDPRO_OT_Start(bpy.types.Operator):
    bl_idname = "blendpro.start"
    bl_label = "Start BlendPro server"

    def execute(self, context):
        start_server()
        return {"FINISHED"}


class BLENDPRO_OT_Stop(bpy.types.Operator):
    bl_idname = "blendpro.stop"
    bl_label = "Stop BlendPro server"

    def execute(self, context):
        stop_server()
        return {"FINISHED"}


class BLENDPRO_PT_Panel(bpy.types.Panel):
    bl_label = "BlendPro MCP"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendPro"

    def draw(self, context):
        lay = self.layout
        srv = getattr(bpy.types, "blendpro_server", None)
        if srv and srv.running:
            lay.label(text=f"Running on port {srv.port}", icon="CHECKMARK")
            lay.operator("blendpro.stop")
        else:
            lay.label(text="Server stopped", icon="ERROR")
            lay.operator("blendpro.start")
        sc = context.scene
        box = lay.box()
        box.label(text="Asset integrations (blendmcp)")
        for prop, label in (("blendermcp_use_polyhaven", "Poly Haven"), ("blendermcp_use_sketchfab", "Sketchfab"),
                            ("blendermcp_use_hyper3d", "Hyper3D Rodin"), ("blendermcp_use_hunyuan3d", "Hunyuan3D")):
            if hasattr(sc, prop):
                box.prop(sc, prop, text=label)
        if getattr(sc, "blendermcp_use_sketchfab", False):
            box.prop(sc, "blendermcp_sketchfab_api_key", text="Sketchfab key")
        if getattr(sc, "blendermcp_use_hyper3d", False):
            box.prop(sc, "blendermcp_hyper3d_mode", text="Rodin mode")
            box.prop(sc, "blendermcp_hyper3d_api_key", text="Rodin key")


def _register_legacy_props():
    P = bpy.props
    defs = {
        "blendermcp_use_polyhaven": P.BoolProperty(name="Use Poly Haven", default=False),
        "blendermcp_use_hyper3d": P.BoolProperty(name="Use Hyper3D Rodin", default=False),
        "blendermcp_hyper3d_mode": P.EnumProperty(name="Rodin Mode", items=[("MAIN_SITE", "hyper3d.ai", ""), ("FAL_AI", "fal.ai", "")], default="MAIN_SITE"),
        "blendermcp_hyper3d_api_key": P.StringProperty(name="Hyper3D API Key", subtype="PASSWORD", default=""),
        "blendermcp_use_sketchfab": P.BoolProperty(name="Use Sketchfab", default=False),
        "blendermcp_sketchfab_api_key": P.StringProperty(name="Sketchfab API Key", subtype="PASSWORD", default=""),
        "blendermcp_use_hunyuan3d": P.BoolProperty(name="Use Hunyuan 3D", default=False),
        "blendermcp_hunyuan3d_mode": P.EnumProperty(name="Hunyuan3D Mode", items=[("LOCAL_API", "local api", ""), ("OFFICIAL_API", "official api", "")], default="LOCAL_API"),
        "blendermcp_hunyuan3d_secret_id": P.StringProperty(name="SecretId", default=""),
        "blendermcp_hunyuan3d_secret_key": P.StringProperty(name="SecretKey", subtype="PASSWORD", default=""),
        "blendermcp_hunyuan3d_api_url": P.StringProperty(name="API URL", default="http://localhost:8081"),
        "blendermcp_hunyuan3d_octree_resolution": P.IntProperty(name="Octree Resolution", default=256, min=128, max=512),
        "blendermcp_hunyuan3d_num_inference_steps": P.IntProperty(name="Steps", default=20, min=20, max=50),
        "blendermcp_hunyuan3d_guidance_scale": P.FloatProperty(name="Guidance", default=5.5, min=1.0, max=10.0),
        "blendermcp_hunyuan3d_texture": P.BoolProperty(name="Generate Texture", default=False),
    }
    added = []
    for k, v in defs.items():
        if not hasattr(bpy.types.Scene, k):
            setattr(bpy.types.Scene, k, v)
            added.append(k)
    return added


_CLASSES = (BLENDPRO_AP_Prefs, BLENDPRO_OT_Start, BLENDPRO_OT_Stop, BLENDPRO_PT_Panel)
_added_props = []


def _autostart():
    p = _prefs()
    if p is None or p.autostart:
        try:
            start_server()
        except OSError as e:
            print(f"[BlendPro] could not start server: {e}")
    return None


def register():
    global _added_props
    for c in _CLASSES:
        bpy.utils.register_class(c)
    _added_props = _register_legacy_props()
    if not bpy.app.background:
        bpy.app.timers.register(_autostart, first_interval=1.0)


def unregister():
    stop_server()
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    for k in _added_props:
        try:
            delattr(bpy.types.Scene, k)
        except Exception:
            pass
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
