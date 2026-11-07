# Texture Painting & Masking

1. **Face Masking**: In Texture Paint mode, click the small "Face Selection" icon next to the mode dropdown. You can now tab into Edit Mode, select specific faces, and when you tab back to Texture Paint, you will *only* be able to paint on the selected faces.
2. **Gradient Painting**: Set the fill bucket tool to "Gradient". Choose your two colors and drag across your mesh to apply a smooth stylized gradient (perfect for bird bodies or clothing).
3. **Overlaying Line Art**: 
   - In the Shader Editor, add a second `Image Texture` with a transparent Alpha background.
   - Use a `Mix Color` node to lay it over your base color, using the Alpha output of the new texture as the Factor.
   - You can now paint sharp line-art details, wrinkles, or facial features non-destructively on a separate layer.
