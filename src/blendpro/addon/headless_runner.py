"""Run inside `blender --background [file.blend] --python headless_runner.py -- --port N`.

Serves BlendPro commands on the main thread (no UI, no timers needed)."""
import importlib
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(here))
addon = importlib.import_module(os.path.basename(here))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 9878

addon._register_legacy_props()
addon.Server(port=port).serve_blocking()
