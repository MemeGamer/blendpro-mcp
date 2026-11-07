# Basic Character Modeling Workflows

1. **SubD Blockout**: Start with a default cube. Add a Subdivision Surface modifier (Level 2). Tab into edit mode and move vertices to shape a rounded, stylized body. Once satisfied, use `Object -> Convert to Mesh` to bake the topology.
2. **Shrinkwrap Wings/Armor**: Model a flat piece of armor or wings. Add a Shrinkwrap modifier targeting the body to pull it flush. Then add a Solidify modifier to give it thickness, and a Subdivision modifier to smooth it.
3. **The Eye Pancake Trick**: Spawn a sphere. In Edit Mode side-view, delete the entire back half. Press `S -> Y -> 0` to flatten the remaining dome into a perfect 2D pancake. 
4. **Tangent Normal Project**: To stick cartoon eyes perfectly onto a curved head, use a Shrinkwrap modifier on the flattened eye and set the wrap method to `Tangent Normal Project`. Add Solidify to make it pop out slightly. 
5. **Cylinders over Cones**: When making hats or beaks, do not use the Cone primitive (the single pole at the top pinches under Subdivision). Use a Cylinder with ~16 sides, scale the top loop inwards, and delete the top cap before subdividing.
