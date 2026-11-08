# Donut, modeled from reference images

Claude Code built this donut in a live Blender 5.2 window through BlendPro. It used the reference-image recipes (`reference_measure`, `reference_image_setup`, `reference_details` and `reference_compare`) and recorded the build with OBS.

| File | What it is |
|---|---|
| `donut.blend` | The finished scene. It contains the dough, the glaze (with Shrinkwrap, Solidify, SubD and the drips), 12 sprinkle variants scattered by Geometry Nodes, the plate, lights and camera. The three `REF_*` image empties are hidden; unhide them to see the overlay. Image paths are relative. |
| `ref/donut_top.png`, `ref/donut_front.png`, `ref/donut_left.png` | The orthographic reference views, generated with ChatGPT image generation. |
| `donut_reel.mp4` | 22 s vertical reel (1080×1920), with the voiceover recorded in ElevenLabs. |

## Measured from the references (real width set to 10 cm)

| Measurement | Value |
|---|---|
| Outer diameter / hole / height | 10.0 cm / 2.6 cm / 4.4 cm |
| Glaze colour (front view, top band) | `#ee6d8e` (the build used the palette's dominant `#f85a8f`) |
| Sprinkles visible in the top view | 112, median length 4.9 mm (p10–p90: 1.6–9.5 mm), median width 1.4 mm |

## Score against the references (`reference_compare`, silhouette IoU)

| View | Blockout only | Final (dough + glaze + drips + sprinkles) |
|---|---|---|
| Front | 0.949 | **0.955** |
| Left | 0.945 | **0.960** |
| Top | 0.970 | **0.943** |

The top-view score drops slightly in the final model: the glaze and sprinkles add about 1 mm around the rim, which the generated top view doesn't show.

## Re-run the score yourself

Open `donut.blend` with BlendPro connected, then:

```
recipes(action="run", name="reference_compare", params={"reference": "REF_front", "objects": ["Donut", "Glaze"]})
```

---

The music in `donut_reel.mp4` is "Happy Beats / Business Moves Vol. 11" by Sascha Ende, [ende.app](https://ende.app/en), licensed [CC BY 4.0](https://ende.app/en/standard-license). It is not covered by this repo's MIT license.
