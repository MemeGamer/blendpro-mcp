# Thin Wall Transparency (Blender 4.2+)

The new "Thin Wall" toggle in the Principled BSDF node completely changes how we handle flat transparent surfaces!

**Why it's amazing:**
Previously, one-sided planes with glass or transmissive materials looked completely broken and dark, forcing you to add a "Solidify" modifier (which doubled geometry and caused Z-fighting).

**Use Cases:**
1. **Windows & Dark Glass**: Instantly fixes the "dark glass" rendering artifact where flat windows absorb all the light. Check "Thin Wall" and your room will flood with accurate light.
2. **Leaves & Foliage**: Double-sided leaves take forever to render. Apply "Thin Wall" to flat leaf planes. It allows sunlight to accurately transmit through the leaves, AND it renders significantly faster.
3. **Soap Bubbles**: Apply to a sphere to calculate transparency based purely on the faces, bypassing solid internal refraction.
4. **Paper**: Perfect for rendering receipts, parchment, or thin fabrics passing light.

**Settings:**
In the Principled BSDF, toggle "Thin Wall". The Subsurface Radius options disappear (as they apply to thick volumes). Adjust the new projection slider (from -1 to 1) to determine which side of the flat plane the light is biased toward.
