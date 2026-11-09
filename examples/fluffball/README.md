# Fluffball: squash & stretch + cute faces + simulated fur

| File | What it is |
|---|---|
| `fluffball.blend` | The full scene (Blender 5.2). It has the `FluffRig` squash & stretch rig, the `Fluff` body with particle-hair fur, the `FaceDecal` driven by its `expression` property, the face view-layer setup and the compositor tree |
| `fluffball.mp4` | The 4 s render (1920×1080, 30 fps, 120 frames, EEVEE) |

## Re-render it
The hair-dynamics cache isn't included, because it's about 97 MB.

1. Open the file and go to `Fluff → Particles → FurSettings → Cache`.
2. Turn on **Disk Cache** and press **Bake** (or run `bpy.ops.ptcache.bake(bake=True)` with that point cache in the context). This step matters: stills rendered out of order show stale hair.
3. Render the animation: `blender -b fluffball.blend --render-anim`.

## Change the performance
- **Expressions:** keyframe `FaceDecal["expression"]` (CONSTANT keys) or call `face_expression_set` / `face_expression_animate` with names such as `happy`, `squee`, `star_eyes`, `laughing`, `in_love` or `wink`. The full list of 50 is in `docs/images/faces_catalog.png`.
- **Squash and stretch:** move the `top` control. Its location squashes or stretches the body, its rotation bends it, and `root` moves the whole character.
