# Sculpting in Blender for Beginners

A comprehensive guide to sculpting a stylized character, based on CG Boost's tutorial.

## 1. Preparation & Setup
- **Graphics Tablet Settings:** Map the pen buttons to Middle Mouse (lower button) and Right Click (upper button). Use "Orbit around selection", "Depth", and "Zoom to Mouse position" in Blender's Navigation preferences.
- **Reference Setup:** 
  - Add your reference sketch as an Image Empty in Object Mode.
  - Set it to "Front" depth and lower opacity (~0.3).
  - Uncheck "Perspective" in the image settings so it only appears in orthographic views (Front/Side), keeping your perspective view clear.
- **Scale Warning:** If you scale your base mesh primitives in Object mode, **ALWAYS** press `Ctrl + A -> Apply Scale` before entering Sculpt mode. Uneven scale squashes and stretches your brush strokes.

## 2. Blockout & Remeshing
- Block out your base shape using simple primitives (Cylinders for the head/neck, Sphere/Hemisphere for the body).
- **Voxel Remesher:** In Sculpt Mode, press `R` to visually adjust the voxel grid size (hold `Shift` for fine control). Start at a low resolution like `0.03` for the base blockout. Click `Ctrl + R` to execute the remesh.
- Keep resolution low during the blockout phase to make big shape adjustments easier!

## 3. Core Brushes & Shortcuts
- **F**: Change Brush Radius.
- **Shift + F**: Change Brush Strength.
- **Ctrl**: Invert brush action (e.g., Subtract instead of Add).
- **Shift**: Temporarily switch to the Smooth brush.
- **Grab Brush:** Essential for pushing and pulling large proportions.
- **Clay Strips:** Good for building up volume (adding clay) or carving out chunks.
- **Scrape Brush:** Crucial for stylized, carved-stone looks. Flattens wobbly, organic surfaces into planar, sharp shapes.
- **Draw Sharp & Pinch:** Great for carving deep creases (like a mouth line) and pulling geometry tightly together to form sharp edges.

## 4. Masking & Posing
- **Box / Lasso Mask:** Draw masks over areas you want to protect.
- **Invert Mask (`Ctrl + I`)**: Mask everything *except* a specific part (like the nose or ears) to pull it out using the Grab/Move tool.
- **Clear Mask (`Alt + M`)**: Remove the mask.
- **Face Sets:** Create groups of polygons (`Face Set from Masked`) that can easily be hidden or auto-masked during coloring or sculpting.

## 5. Vertex Painting
- **Paint Tool:** Paint color directly onto vertices (requires a dense mesh to look good, which sculpting provides).
- **Color Filter Tool:** Instantly fill the entire mesh, or adjust Hue/Saturation/Value.
- **Cavity Auto-Masking:** Enable Cavity in the color filter tool to automatically paint *only* in the crevices (darkening shadows) or invert it to paint *only* the raised edges (highlighting edges). 
