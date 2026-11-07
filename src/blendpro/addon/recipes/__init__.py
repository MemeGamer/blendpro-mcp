"""Recipe registry: tested, parameterized one-call Blender tasks.

Each recipe module does:
    from . import recipe
    @recipe("modeling", tags=("hard-surface",))
    def bolt(name="Bolt", length=0.05, location=(0, 0, 0)) -> dict:
        '''One-line summary. Longer notes...'''
        ...
        return {"object": obj.name, ...}   # compact, JSON-serialisable
"""
import importlib
import inspect
import pkgutil

REGISTRY = {}
_loaded = False
_load_errors = {}


def recipe(category, tags=()):
    def deco(fn):
        doc = inspect.getdoc(fn) or ""
        prev = REGISTRY.get(fn.__name__)
        if prev and prev["module"] != fn.__module__:
            _load_errors[f"duplicate:{fn.__name__}"] = f"{fn.__module__} overrides {prev['module']}"
        REGISTRY[fn.__name__] = {"fn": fn, "category": category, "tags": tuple(tags), "doc": doc,
                                 "summary": doc.splitlines()[0] if doc else "", "module": fn.__module__}
        return fn
    return deco


def load_all(reload=False):
    global _loaded
    if _loaded and not reload:
        return
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("_"):
            continue
        full = f"{__name__}.{m.name}"
        try:
            mod = importlib.import_module(full)
            if reload:
                importlib.reload(mod)
        except Exception as e:  # one broken module must not hide the rest
            _load_errors[m.name] = f"{type(e).__name__}: {e}"
    _loaded = True


def _sig(fn):
    try:
        return str(inspect.signature(fn)).replace(" -> dict", "")
    except Exception:
        return "(...)"


def recipes(action="list", name=None, params=None, category=None, query=None):
    """list (optionally by category/query) | doc {name} | run {name, params}."""
    load_all()
    if action == "list":
        cats = {}
        for n, r in sorted(REGISTRY.items()):
            if category and r["category"] != category:
                continue
            if query:
                q = query.lower()
                if q not in n.lower() and q not in r["doc"].lower() and not any(q in t for t in r["tags"]):
                    continue
            cats.setdefault(r["category"], []).append(f"{n}: {r['summary']}")
        out = {"count": sum(len(v) for v in cats.values()), "recipes": cats,
               "hint": "recipes(action='doc', name=...) for parameters; action='run' to execute"}
        if _load_errors:
            out["load_errors"] = _load_errors
        return out
    if name not in REGISTRY:
        import difflib
        raise ValueError(f"unknown recipe {name!r}. Close: {difflib.get_close_matches(name or '', list(REGISTRY), n=6, cutoff=0.4)}")
    r = REGISTRY[name]
    if action == "doc":
        return {"name": name, "category": r["category"], "signature": name + _sig(r["fn"]), "doc": r["doc"], "tags": r["tags"]}
    if action == "run":
        from .. import scene as scn
        before = scn.snapshot()
        result = r["fn"](**(params or {}))
        import bpy
        bpy.context.view_layer.update()
        return {"recipe": name, "result": result, "diff": scn.diff(before, scn.snapshot())}
    raise ValueError("action must be list, doc or run")
