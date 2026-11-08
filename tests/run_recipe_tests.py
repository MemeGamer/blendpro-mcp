"""Run recipe tests inside Blender:

    blender --background --factory-startup --python tests/run_recipe_tests.py -- [module ...]

Each tests/test_<module>.py defines CASES = [(recipe_name, params, check_fn_or_None[, setup_fn]), ...].
Every case runs on a fresh empty scene. check_fn(result_dict) must return True (or raise)."""
import importlib.util
import json
import os
import sys
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "blendpro"))
import addon  # noqa: E402
from addon import recipes as R  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
R.load_all()
if R._load_errors:
    print("LOAD ERRORS:", json.dumps(R._load_errors, indent=1))

files = [f for f in sorted(os.listdir(HERE)) if f.startswith("test_") and f.endswith(".py")]
if argv:
    files = [f for f in files if f[5:-3] in argv]
passed = failed = 0
fails = []
for f in files:
    spec = importlib.util.spec_from_file_location(f[:-3], os.path.join(HERE, f))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for case in mod.CASES:
        name, params, check, setup = (list(case) + [None, None])[:4]
        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            if setup is not None:  # optional scene preparation before the recipe runs
                setup()
            res = R.recipes("run", name=name, params=params)
            json.dumps(res, default=str)  # must be serialisable
            if check is not None and not check(res["result"]):
                raise AssertionError(f"check failed: {str(res['result'])[:300]}")
            passed += 1
        except Exception as e:
            failed += 1
            fails.append((f, name, f"{type(e).__name__}: {e}", traceback.format_exc()[-1200:]))
for f, name, err, tb in fails:
    print(f"FAIL {f}::{name}: {err}\n{tb}")
print(f"RESULT passed={passed} failed={failed} registered={len(R.REGISTRY)}")
sys.stdout.flush()
os._exit(0 if failed == 0 else 1)
