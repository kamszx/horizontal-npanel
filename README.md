# Blender Horizontal N-Panel Tabs

A lightweight runtime UI patch / add-on for Blender 4.0+ that replaces the default vertical, rotated N-Panel tabs with a clean, readable horizontal tab list.

## Features
- **Horizontal Text Layout:** Reads naturally top-to-bottom without neck-tilting.
- **Native Integration:** Operates directly on the native N-Panel categories without breaking third-party add-ons (HardOps, BoxCutter, UVPackmaster, etc.).
- **Tab Management:**
  - **Pin** favorite tabs to the top.
  - **Reorder** tabs up and down.
  - **Hide** unused tabs with a collapsible recovery drawer (`Hidden`).
- **Persistent Config:** Automatically saves your layout per editor in Blender's user config directory.
- **Supported Editors:** 3D Viewport, UV / Image Editor, and Node Editors (Shader, Geometry, Compositor, Texture).

## Installation

### Method 1: As a Standard Add-on (Recommended)
1. Download `npanel_horizontal_tabs.py` from the [Releases](../../releases) page.
2. In Blender, go to **Edit → Preferences → Add-ons**.
3. Click **Install from Disk...** (or simply drag & drop the `.py` file into Blender) and enable **Horizontal N-Panel Tabs (Native Patch)**.

### Method 2: Startup Script (Always Active)
Place `npanel_horizontal_tabs.py` directly into your Blender startup directory:
- **Windows:** `%APPDATA%\Blender Foundation\Blender\<version>\scripts\startup\`

## License
Free for personal and commercial use. Redistribution and reselling are prohibited. See [LICENSE](LICENSE) for details.