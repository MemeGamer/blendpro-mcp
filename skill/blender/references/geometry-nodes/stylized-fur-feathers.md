# Stylized Fur and Feathers using Geometry Nodes

Create stylized, low-poly fur and feathers without the ugly intersection shadows!

**1. The Setup**
Use a "Scatter on Surface" modifier (or standard Instance on Points). Distribute your feather asset across the body object. Align rotation to the normals. 

**2. The Custom Normal Trick (Crucial for Stylized Look)**
The key to avoiding chunky, overlapping shadows on thousands of feathers is to "steal" the smooth normals of a basic sphere.
- Unpack your Geometry Nodes tree.
- Drag a smooth Subdivided Sphere into the Geo Nodes graph (Object Info node).
- Add a `Sample Nearest Surface` node set to 'Vector', plug the sphere geometry in.
- Add a `Normal` node into the Value of the sample.
- Add a `Set Mesh Normal` node (Free mode) at the very end of your node tree, and plug the Sample Nearest Surface vector into the 'Custom Normal' slot.
- Result: All your individual feathers will now shade flawlessly as if they are one continuous smooth sphere!

**3. Geo Node Animation**
- Bring in a `Scene Time` node (Frame output).
- Plug it into a `Math (Divide)` node (set to ~350 for slow movement).
- Combine this with a `Position` node and route it into a `Noise Texture`. 
- Plug this output into the rotation of a `Rotate Instances` node to create a gentle, ruffled wind animation.

**4. Random Color Attribute**
- Right before the feathers are realized, add a `Store Named Attribute` node (Domain: Instance, Data Type: Color). 
- Name it "rand_color".
- Plug a `Random Value` (Float) into the Value. You can now use this in the Shader Editor via an Attribute node!
