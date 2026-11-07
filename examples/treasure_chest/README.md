# Treasure chest example

A stylised treasure chest built by Claude Code through BlendPro in a headless Blender 5.2. The textures came from ChatGPT image generation.

| File | What it is |
|---|---|
| `treasure_chest.glb` | The finished asset: an 8.4 MB, Draco-compressed GLB for WebXR, made with `export_asset(preset="webxr_glb")`, with the textures embedded |
| `steps.json` | One line per stage of the build, in order, written for video captions |

To view the model, drop the GLB into any glTF viewer or open it in Blender with File > Import > glTF 2.0. The README in the repo root has the stage-by-stage images and the token cost.

The `lid_fix` step in `steps.json` is a fix made after review. The first version had the lid's half-cylinder upside down and the body straps crossing the opening. A bounding-box query showed the cause, and a collision check confirmed the fix.
