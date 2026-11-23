# hypr-cava-visualizer

A real-time audio visualizer for [Hyprland](https://hyprland.org/), using [cava](https://github.com/karlstav/cava) for audio analysis, GTK4/Cairo for rendering, and [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) to display a waveform or spectrum bars on a Wayland layer.

This is an **extended version** of [Chiyo-no-sake's original hypr-cava-visualizer](https://github.com/Chiyo-no-sake/hypr-cava-visualizer). The original project provided the GTK4/Cava visualizer, smooth Bézier waveform, animated colors, mirroring, fade, and power-aware operation. This version adds JSON configuration, compact layer placement, rectangular bars, location-dependent reactivity, independent theme colors, and an optional wave-edge glow. **Full credit for the original project and foundation goes to [Chiyo-no-sake](https://github.com/Chiyo-no-sake).** See [Credits and license](#credits-and-license).

<!-- Optional: add a screenshot to your repository and uncomment:
![Visualizer behind a desktop bar](screenshot.png)
-->

## Features

- **Two styles:** smooth Bézier **wave** or discrete **bars**.
- **Positioning:** fullscreen or a fixed-size layer, anchored at the top, bottom, or center; left, center, or right alignment.
- **Direction:** upward or downward audio movement; optional vertical mirroring.
- **JSON configuration** via `--config`, with CLI overrides.
- **Quiet zone:** lower and smooth reactivity in a selected horizontal region; smooth `valley` or `plateau` profile.
- **Edge fade:** smoothly taper the visualizer toward the left and right edges, for both wave and bars.
- **Wave fill and glow:** independent options to fill the waveform and add a subtle colored outline halo.
- Three-color animated gradient, optional saturation boost, and fade on silence.
- Separate **JSON or Hyprland-format color file**, reloaded automatically when modified; suitable for theme generators such as [Unclaimed Bloom](https://github.com/Praczet/unclaimed-bloom) or [matugen](https://github.com/InioX/matugen).
- Configurable Wayland layer (`background`, `bottom`, `top`, `overlay`).
- Cava Monstercat smoothing, noise reduction, sensitivity, framerate detection, and optional power awareness.
- Transparent GTK window styling, including `backdrop-filter: none`, to avoid theme-induced background effects.

## Dependencies

| Dependency | Purpose |
|---|---|
| Python 3.10+ | Runtime |
| GTK4 | Toolkit and drawing surface |
| [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) | Wayland layer surface |
| [cava](https://github.com/karlstav/cava) | Audio capture and frequency analysis |
| PyGObject | GTK4 Python bindings |
| PyCairo | Wave and bar drawing |

### Arch Linux

```sh
sudo pacman -S cava gtk4-layer-shell python-gobject python-cairo
```

### Fedora

```sh
sudo dnf install cava gtk4-layer-shell python3-gobject python3-cairo
```

### Ubuntu / Debian

```sh
sudo apt install cava libgtk-4-1 libgtk4-layer-shell0 python3-gi python3-gi-cairo gir1.2-gtk-4.0
```

Package names and availability can vary by distribution.

## Installation and running

Keep `hypr-cava-visualizer.py` and your JSON files together or organize the JSON files under `~/.config/hypr-cava/`. Run the extended Python script directly:

```sh
LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so \
GDK_BACKEND=wayland \
python3 hypr-cava-visualizer.py --config ./qreep-cava.json
```

**Why `LD_PRELOAD`?** Depending on your GTK/Python installation, `gtk4-layer-shell` must be loaded *before* the Wayland client library. If the visualizer appears as a normal tiled GTK window with `GtkWindow is not a layer surface` warnings, start it with `LD_PRELOAD` as shown above. The example path is for Arch Linux; locate the library on other distributions using:

```sh
find /usr/lib /usr/lib64 -name 'libgtk4-layer-shell.so*' 2>/dev/null
```

The [original upstream repository](https://github.com/Chiyo-no-sake/hypr-cava-visualizer) also provides a `make install` workflow and a launcher wrapper. If using the upstream wrapper, ensure it launches **this extended script** rather than the upstream version; the new options are not necessarily available in an unmodified upstream installation.

### Fullscreen / original behavior

```sh
LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so GDK_BACKEND=wayland \
  python3 hypr-cava-visualizer.py
```

With no config or new positioning flags, the visualizer retains fullscreen waveform defaults.

## Configuration

Create a `qreep-cava.json` file:

```json
{
  "bars": 65,
  "framerate": 0,
  "anchor": "top",
  "width": 800,
  "height": 120,
  "align": "center",
  "direction": "down",
  "style": "wave",
  "bar_gap": 3,
  "height_pct": 100,
  "mirror": false,
  "layer": "bottom",
  "opacity": 0.55,
  "fade": true,
  "sensitivity": 150,
  "reactivity": 1.0,
  "quiet_zone": {
    "start": 0.20,
    "end": 0.80,
    "reactivity": 0.65,
    "smoothing": 0.65,
    "profile": "valley"
  },
  "edge_fade": {
    "width": 0.15,
    "reactivity": 0.0,
    "smoothing": 0.65
  },
  "wave_fill": true,
  "wave_glow": true,
  "glow_width": 8,
  "glow_opacity": 0.18,
  "colors_file": "qreep-cava-colors.json"
}
```

Start it with `--config` and optionally override ordinary settings from the command line:

```sh
python3 hypr-cava-visualizer.py --config qreep-cava.json --style bars --bar-gap 4
```

For direct execution, add the `LD_PRELOAD` and `GDK_BACKEND` environment variables as shown above if required by your installation.

**Configuration rules:**

- JSON keys use underscores (`wave_glow`, `height_pct`, `colors_file`), corresponding to dashed CLI flags.
- CLI flags override the values loaded from the JSON config.
- `colors_file`, when specified in the JSON, is resolved relative to the main configuration file.
- Unknown keys and invalid configuration values cause a startup error rather than being silently ignored.
- `quiet_zone` and `edge_fade` are **nested JSON-only** settings. Omit an object to disable that feature.

### Quiet zone and edge fading

`quiet_zone` shapes amplitude and temporal smoothing in a horizontal region (`0.0` is the left edge; `1.0` is the right edge). It affects **both wave and bars**:

| Key | Meaning |
|---|---|
| `start`, `end` | Fractional horizontal limits of the zone |
| `reactivity` | Amplitude multiplier in the quietest part; `0` suppresses activity there |
| `smoothing` | Temporal smoothing amount from `0` (responsive) toward `1` (slower) |
| `profile` | `valley`: smooth dip toward the center; `plateau`: a flatter quiet region |
| `transition` | Optional soft boundary width for `plateau` (fraction of total width) |

The **`valley` profile** makes a smooth reduction toward the center and avoids sudden vertical cutoffs. The **`plateau` profile** is useful when you want a broad quiet section; add `transition` to soften its sides.

`edge_fade` is independent of the center quiet zone:

| Key | Meaning |
|---|---|
| `width` | Fraction of the visualization width used for tapering at **each** edge (`0`–`0.5`) |
| `reactivity` | Amplitude multiplier at the very edge; `0` makes the waveform meet the baseline |
| `smoothing` | Extra temporal smoothing applied near the edges |

For a bar behind floating controls, a gentle `valley` in the center plus an `edge_fade` at both ends often looks more natural than a completely empty central gap.

### Wave fill and glow

```json
{
  "style": "wave",
  "wave_fill": true,
  "wave_glow": true,
  "glow_width": 8,
  "glow_opacity": 0.18
}
```

`wave_fill` toggles the area fill underneath the smooth curve. Set it to `false` for a line-only waveform. `wave_glow` draws several translucent Cairo strokes around the waveform outline; `glow_width` adjusts their size and `glow_opacity` their intensity (`0`–`1`). The glow uses the **primary** theme color and is independent of Hyprland blur.

For rectangular bars, select `"style": "bars"`; `bar_gap` sets the gap between columns in logical pixels. Wave fill and glow only affect wave rendering.

### Separate colors JSON for theme integration

Create `qreep-cava-colors.json` alongside the main config:

```json
{
  "primary": "#B2B992",
  "secondary": "#DEDFE2",
  "tertiary": "#719B91"
}
```

Because the main config uses `"colors_file": "qreep-cava-colors.json"`, a theme generator can rewrite this small palette without touching visualizer behavior. The visualizer polls the color file for changes approximately every two seconds and reloads it while running.

The legacy Hyprland format remains supported in a non-JSON file:

```conf
$primary = rgb(B2B992)
$secondary = rgb(DEDFE2)
$tertiary = rgb(719B91)
```

If no colors file is provided, the visualizer uses `--color-primary`, `--color-secondary`, and `--color-tertiary` (or their defaults).

## CLI options

The main new options are:

| Flag | Default | Meaning |
|---|---|---|
| `--config PATH` | none | Main JSON configuration |
| `--style wave\|bars` | `wave` | Waveform or columns |
| `--anchor top\|bottom\|center` | `center` | Vertical placement of a bounded layer |
| `--align left\|center\|right` | `center` | Horizontal placement |
| `--width N` | `0` | Layer width in logical pixels; `0` stretches across monitor |
| `--height N` | `0` | Layer height in logical pixels; `0` stretches across monitor |
| `--direction up\|down` | `up` | Direction from the baseline |
| `--bar-gap N` | `3` | Gap between bars in logical pixels |
| `--reactivity N` | `1.0` | Overall amplitude multiplier |
| `--wave-fill` / `--no-wave-fill` | enabled | Filled wave or outline only |
| `--wave-glow` / `--no-wave-glow` | disabled | Outline halo |
| `--glow-width N` | `9` | Halo width |
| `--glow-opacity N` | `0.22` | Halo strength |

Original controls are also available:

| Flag | Default | Meaning |
|---|---|---|
| `--bars N` | `50` | Number of Cava frequency bands |
| `--framerate N` | `0` | Target FPS, `0` for automatic selection |
| `--height-pct N` | `20` | Amplitude height as a percentage of the drawing area |
| `--opacity N` | `0.55` | Visualization opacity |
| `--mirror` / `--no-mirror` | mirrored | Symmetrical vertical display |
| `--layer LAYER` | `background` | `background`, `bottom`, `top`, `overlay` |
| `--monstercat` / `--no-monstercat` | enabled | Cava Monstercat smoothing |
| `--noise-reduction N` | `0.50` | Cava noise reduction |
| `--sensitivity N` | `150` | Cava sensitivity |
| `--gradient-speed N` | `0.15` | Gradient cycles per second |
| `--colors-file PATH` | none | JSON or Hyprland-format palette |
| `--color-primary HEX` | `#F2BF6E` | First gradient color |
| `--color-secondary HEX` | `#E6BDC0` | Second gradient color |
| `--color-tertiary HEX` | `#B5CF9E` | Third gradient color |
| `--fade` / `--no-fade` | disabled | Fade in/out on silence |
| `--fade-in-speed N` | `3.0` | Fade-in speed |
| `--fade-out-speed N` | `1.5` | Fade-out speed |
| `--silence-threshold N` | `0.02` | Silence threshold |
| `--boost-saturation N` | `0.35` | Saturation enhancement |
| `--power-aware` / `--no-power-aware` | disabled | Limit auto-framerate based on power profile |
| `--cava-config-dir PATH` | runtime directory | Location of generated Cava configuration |

Use `python3 hypr-cava-visualizer.py --help` for the current CLI help.

## Hyprland and desktop bar integration

To display an 800px-wide visualizer **behind** a floating Qreep top bar, use `"width": 800`, `"anchor": "top"`, `"align": "center"`, and `"layer": "bottom"`. Keep the Qreep bar on the `top` layer. The visualizer does not reserve screen space (`exclusive_zone = -1`).

Check the layer order with:

```sh
hyprctl layers
```

The expected relationship is:

```text
background: wallpaper
bottom:     hypr-cava-visualizer
top:        qreep-bar
```

Your desktop bar's own background must also be appropriately transparent if you want the waveform visible behind it. The visualizer applies transparent GTK styling, including `backdrop-filter: none`, to avoid theme backdrops producing an unwanted dark rectangle; this does **not** require enabling Hyprland layer blur.

For autostart, use the same working command you tested manually in your Hyprland startup setup or a user systemd service. Keep `LD_PRELOAD` if your Python/GTK environment requires it.

## Performance

The rendering is 2D Cairo/GTK4; audio analysis runs in Cava. Actual CPU/GPU usage depends on frame rate, bar count, audio activity, compositor effects and hardware. For a compact top bar, try 30 FPS (`"framerate": 30`) if you want to reduce work. Compare usage with `btop`, `pidstat`, and a GPU monitor appropriate for your system.

## Credits and license

**Original author: [Chiyo-no-sake](https://github.com/Chiyo-no-sake)**  
**Original project: [Chiyo-no-sake/hypr-cava-visualizer](https://github.com/Chiyo-no-sake/hypr-cava-visualizer)**

This project is built on Chiyo-no-sake's original audio visualizer. Credit for the foundation—including the GTK4 layer-shell integration, Cava-based audio visualization, smooth Bézier wave rendering, and the original gradient/animation functionality—belongs to the upstream author. The layout, JSON configuration, bars, quiet-zone/edge-fade controls, theme palette integration, and optional glow described here are additions made in this extended version.

Please retain the original author's attribution and license notices when redistributing or modifying the code. The upstream README identifies the project as **MIT-licensed**; consult the original [`LICENSE`](https://github.com/Chiyo-no-sake/hypr-cava-visualizer/blob/main/LICENSE) and include the applicable license text with redistributed copies.

Related upstream libraries and projects: [Cava](https://github.com/karlstav/cava), [GTK4 Layer Shell](https://github.com/wmww/gtk4-layer-shell), [Hyprland](https://hyprland.org/).
