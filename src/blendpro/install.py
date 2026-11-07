"""`blendpro install-addon [--blender PATH] [--no-enable]`: copy the add-on into Blender and enable it."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ADDON_SRC = Path(__file__).parent / "addon"
ADDON_NAME = "blendpro_mcp"

_ENABLE = r'''
import bpy, addon_utils, sys
print("ADDONS_DIR=" + bpy.utils.user_resource('SCRIPTS', path='addons', create=True))
if "--enable" in sys.argv:
    addon_utils.enable("blendpro_mcp", default_set=True, persistent=True)
    bpy.ops.wm.save_userpref()
    print("ENABLED=" + str("blendpro_mcp" in bpy.context.preferences.addons))
'''


def _run(blender, enable):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(_ENABLE)
        script = f.name
    try:
        args = [blender, "--background", "--python", script, "--"] + (["--enable"] if enable else [])
        res = subprocess.run(args, capture_output=True, text=True, timeout=180)
        return res.stdout + res.stderr
    finally:
        os.unlink(script)


def main(argv):
    from .server import _find_blender
    blender = argv[argv.index("--blender") + 1] if "--blender" in argv else _find_blender()
    if not blender:
        print("Blender not found; pass --blender PATH")
        return 1
    log = _run(blender, enable=False)
    line = next((l for l in log.splitlines() if l.startswith("ADDONS_DIR=")), None)
    if not line:
        print(log[-2000:])
        return 1
    dest = Path(line.split("=", 1)[1]) / ADDON_NAME
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ADDON_SRC, dest, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"copied add-on to {dest}")
    if "--no-enable" not in argv:
        log = _run(blender, enable=True)
        ok = "ENABLED=True" in log
        print("enabled in preferences" if ok else "enable failed:\n" + log[-2000:])
        if not ok:
            return 1
    print("Restart Blender (or disable/enable 'BlendPro MCP') to load the new version.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
