"""Tripo3D API v3 integration: generate / post-process models and import them into Blender.

API key: env TRIPO_API_KEY, or the file ~/.blendpro/tripo_api_key.txt (never passed through the model).
Docs: https://developers.tripo3d.com/en/docs/introduction"""
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import httpx

BASE = os.getenv("TRIPO_BASE_URL", "https://openapi.tripo3d.com/v3")
KEY_FILE = Path.home() / ".blendpro" / "tripo_api_key.txt"
DEFAULT_MODEL = os.getenv("TRIPO_MODEL", "v3.1-20260211")
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "blendpro_tripo"
TERMINAL = {"success", "failed", "cancelled", "banned", "expired"}

# operation -> (path, how the model input is passed)
PROCESS = {
    "texture": "/models/texture",
    "retopo": "/mesh/decimate",
    "convert": "/models/convert",
    "rig_check": "/animations/rig-check",
    "rig": "/animations/rig",
    "retarget": "/animations/retarget",
    "segment": "/mesh/segment",
    "complete": "/mesh/complete",
    "uv": "/mesh/uv",
}

PRESETS = {
    # quick ways to express intent without memorizing parameters
    "draft": {"texture": False, "pbr": False, "export_uv": False},
    "standard": {},
    "detailed": {"texture_quality": "detailed", "geometry_quality": "detailed"},
    "game_ready": {"smart_low_poly": True, "face_limit": 10000, "auto_size": True},
    "xr_lowpoly": {"smart_low_poly": True, "face_limit": 5000, "auto_size": True, "texture_quality": "standard"},
    "quad": {"quad": True, "face_limit": 10000, "auto_size": True},
}


class TripoError(RuntimeError):
    pass


def api_key() -> str:
    k = os.getenv("TRIPO_API_KEY", "").strip()
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text(encoding="utf-8").strip()
    if not k:
        raise TripoError(f"No Tripo API key. Put it in {KEY_FILE} (one line) or set TRIPO_API_KEY. "
                         "Create a key in the Tripo API console (platform.tripo3d.ai). API credits may be billed "
                         "separately from a Tripo Studio subscription - check tripo_task(action='balance').")
    return k


def _client():
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {api_key()}"}, timeout=60)


def _unwrap(resp: httpx.Response) -> Any:
    try:
        body = resp.json()
    except Exception:
        raise TripoError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400 or body.get("code", 0) != 0:
        msg = body.get("message") or body
        sug = body.get("suggestion")
        raise TripoError(f"Tripo error {body.get('code')} (HTTP {resp.status_code}): {msg}" + (f" | suggestion: {sug}" if sug else ""))
    return body.get("data")


def balance() -> dict:
    with _client() as c:
        return _unwrap(c.get("/account/balance"))


def upload(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise TripoError(f"file not found: {path}")
    with _client() as c, open(p, "rb") as f:
        data = _unwrap(c.post("/files", files={"file": (p.name, f)}, timeout=300))
    return data["file_token"]


def resolve_input(value: str) -> str:
    """Local path -> uploaded file token; URLs, file tokens and task ids pass through."""
    if value and os.path.exists(value):
        return upload(value)
    return value


def create(path: str, body: dict) -> str:
    with _client() as c:
        return _unwrap(c.post(path, json=body))["task_id"]


def get_task(task_id: str) -> dict:
    with _client() as c:
        return _unwrap(c.get(f"/tasks/{task_id}"))


def wait(task_id: str, timeout: float = 240, interval: float = 4) -> dict:
    deadline = time.time() + timeout
    t = get_task(task_id)
    while t.get("status") not in TERMINAL and time.time() < deadline:
        time.sleep(interval)
        t = get_task(task_id)
    return t


def download(task: dict, prefer: str = "model_url") -> str:
    out = task.get("output") or {}
    url = out.get(prefer) or next((v for k, v in out.items() if k.endswith("model_url") and v), None)
    if not url:
        raise TripoError(f"task has no model url; output keys: {list(out)}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1] or ".glb"
    dest = DOWNLOAD_DIR / f"{task['task_id']}{ext}"
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    if ext == ".zip":
        import zipfile
        folder = DOWNLOAD_DIR / task["task_id"]
        with zipfile.ZipFile(dest) as z:
            for n in z.namelist():  # zip-slip guard
                if os.path.isabs(n) or ".." in Path(n).parts:
                    raise TripoError("unsafe path in zip")
            z.extractall(folder)
        model = next((p for p in folder.rglob("*") if p.suffix.lower() in (".fbx", ".glb", ".gltf", ".obj")), None)
        if not model:
            raise TripoError("zip contained no model")
        return str(model)
    return str(dest)


def summarize(task: dict) -> dict:
    keep = {k: task.get(k) for k in ("task_id", "type", "status", "progress", "credits_consumed", "error_code", "error_message") if task.get(k) is not None}
    out = task.get("output") or {}
    if out:
        keep["outputs"] = sorted(out)
        if out.get("rendered_image_url"):
            keep["preview_url"] = out["rendered_image_url"]
    return keep
