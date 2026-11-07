# Stylized Rendering Settings in Eevee

When trying to get a flat, stylized, or illustrated look in Eevee, realistic physics often ruin the illusion.

**1. Ray Tracing**: For stylized fur, feathers, or cel-shaded characters, turn OFF Ray Tracing in the render properties. Ray tracing attempts to cast realistic micro-shadows that will look like dirt or dark blotches on stylized textures.

**2. Fast GI Approximation**: If you *must* use Global Illumination but want it softer:
- Change the Rays to a very low number (e.g., 1 to 4).
- Lower the Thickness to `0.1`.
- Raise the Bias to `0.25`.
This smooths out the bouncing light calculation, giving you gentle color blending without harsh occlusion.

**3. Artificial Rim Lighting via Shader**: Don't rely purely on scene lights for edge highlights. Use a `Layer Weight (Fresnel)` node plugged into a Color Ramp, and feed it into a Hue/Saturation node. This lets you artificially inject bright, saturated rim colors directly into the material's edges, ensuring it pops from every angle.
