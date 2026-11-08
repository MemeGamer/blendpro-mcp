# Reference-image pipeline: model from orthographic views

Use this when the user wants an object to look like a real thing (food, props, products) rather than a
generic primitive. Instead of guessing proportions, you measure them from reference images, model with real
tools, and score the result against the images.

## 1. Get the reference views

Decide how many views you need:

| Object | Views |
|---|---|
| Rotationally symmetric or simple (donut, cup, bottle, ball, lamp) | front, left, top |
| Asymmetric or complex (character, vehicle, shoe, tool) | front, left, top, plus right, back, bottom |

**Generate them with GPT image generation.**
- If the user has ChatGPT open in a linked browser, generate the images there and save them to `<project>/ref/<object>_<view>.png`.
- If no account is linked, give the user the prompts below to paste into ChatGPT (chatgpt.com, image generation). Ask them to save the results to that folder.

Use the same base prompt for every view and change only the view line, so the object stays identical:

```
Orthographic product reference of <OBJECT, with materials, colours and details,
e.g. "a ring donut with glossy pink glaze that drips slightly over the edge in 4-5 short rounded drips,
golden-brown dough with a pale band around the middle, and multicoloured rod sprinkles of varied length">.
VIEW: <exact FRONT view, camera level with the object's centre, looking straight at it |
       exact LEFT side view | exact TOP view looking straight down | exact RIGHT | BACK | BOTTOM view>.
True orthographic projection: no perspective, no tilt, no lens distortion.
Object centred, whole object visible, about 70% of the frame width, same scale as the other views.
Plain flat pure white background (#FFFFFF), no props, no plate, no cast shadow, no text,
soft even studio lighting, photorealistic, square 1:1 image.
```

Tips:
- Generate the top view first. Then ask for the other views "of the same object" in the same chat, so the colours and details carry over.
- Reject images that show perspective (you can see the top surface in a "front" view) or a cast shadow on the background. Both corrupt the silhouette.
- Never invent the object's real size. Ask the user, or use a known real-world size (a ring donut is about 9–10 cm across and 3–3.5 cm tall).

## 2. Measure (text, cheap)

`recipes run reference_measure {filepath, real_width}` for each view returns:
- the silhouette width and height in metres
- a row-by-row width profile; two "runs" on a row means a gap, for example a donut hole in top view
- the hole size
- k-means colours as hex and linear RGBA, ready for Principled Base Color
- the dominant colour of the top, middle and bottom third

In a front view of a glazed object, `bands` separates glaze from base.

`recipes run reference_details {filepath, real_width}` on a view that shows the surface details returns:
- the count and density per m² of small features (sprinkles, seeds, rivets)
- their length and width spread (p10, median, p90) and aspect ratio
- how much their orientation varies
- their own colour palette

Use these numbers to build several detail variants, not one repeated instance.

## 3. Put the views in the scene

`recipes run reference_image_setup {filepath, view, real_width, center, align:"bottom"}` for each view creates an Image empty:
- scaled so the silhouette is at real size, not the whole canvas
- aligned to the view axis (front looks along +Y, top looks down −Z)
- placed in a `References` collection
- unselectable

`depth:"FRONT"` with `opacity` around 0.35 overlays the image on top of the mesh, which works well for tracing in an ortho view. `only_axis_aligned:true` hides it in perspective.

## 4. Model with real tools, driven by the numbers

Block out from the measurements, never by eye:
- Primitive sizes come from the measured width, height and hole size.
- SubD and smooth shading.
- A low-strength Displace with a Clouds texture breaks up the CG perfection.

| Feature | Tool |
|---|---|
| Glaze, icing or coating | Copy the top half of the base mesh, then Shrinkwrap (Outside Surface, small offset), then Solidify (offset −1, so the original surface stays the outside), then SubD |
| Drips, kept subtle | On the coating's boundary loop, pick 4–6 spots that are not evenly spaced. Pull them down along −Z with a falloff over neighbouring vertices: proportional edit, or a Gaussian weight in bmesh by angle around the axis. Vary the length within the reference's range (for a donut, 3–8 mm). Round the tips by letting SubD act on them, and keep the Shrinkwrap so the drips hug the body. |
| Sprinkles and seeds | Make 8–12 variants: lengths from p10..p90, width from the median, colours from the palette, and a slight Simple Deform Bend on some. Scatter with `scatter_with_min_distance`, using a min distance of about 1.2× the median length, aligned to the normal, with random rotation. Centre each variant's mesh on its own origin first (`me.transform(Matrix.Translation(-center))`), or the instances fly off the surface. |
| Colour | Use the palette's linear RGBA directly. Add a Noise multiply (about 0.2) and a Bump so it isn't flat. |

## 5. Score and fix

`recipes run reference_compare {reference:"REF_front"}` renders the model's silhouette from an orthographic camera that frames that reference exactly. It returns:
- `iou`: above 0.92 is a match, above 0.85 is close
- the area ratio and centroid offset
- per-row width error, with the three worst rows

Fix those rows with SubD, proportional edit or Shrinkwrap, then compare again. Do this for each view. It takes no screenshots and costs about 300 tokens per check.

## Gotchas
- Background detection samples the image border. Busy backgrounds, plates or cast shadows break it. Regenerate the image, or pass `threshold=`.
- GPT images are never perfectly orthographic or consistent across views. Treat an IoU around 0.9 as the target, not 1.0.
- Colours from a generated image are display-referred sRGB. The recipe converts them to linear for you. Check under the final lighting and view transform, and lower the saturation if the result looks neon under AgX.
