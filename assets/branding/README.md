# Jarvis branding

## Selected: Neural Orb

Joel selected **Neural Orb** on 25 October 2026. The boot mark is
`jarvis-neural-orb-v1.png`. Its pixels match the selected generation; PNG metadata
was removed for ESPHome compatibility. The exact ImageGen prompt and original
source path are preserved in `jarvis-neural-orb-v1.json`.

This is a static boot mark; voice animations retain their existing artwork and
behavior. Unused logo images, duplicate proposal images and proposal galleries
were removed at Joel's request.

The firmware embeds its exact 320 × 320 RGB565 pixels through LVGL's existing LZ4
decoder in `styles/glass-v2/boot_logo.h`. This reduces resident image bytes from
204,800 to 38,334 without changing the pixels or layout. After changing the selected
PNG, regenerate with `.venv/bin/python tools/pack_boot_logo.py`. The free native
LVGL test checks regeneration, exact decoded pixels and release of decoder memory.
