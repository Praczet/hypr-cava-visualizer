#!/usr/bin/python3
"""
hypr-cava-visualizer -- Real-time audio visualizer for Hyprland.

Renders cava output as smooth bezier curves with animated color gradients
on a Wayland layer surface (behind windows, above wallpaper).

Requires: GTK4, gtk4-layer-shell, cava
"""

import argparse
import atexit
import json
import sys
import os
import select
import signal
import socket
import subprocess
import threading
import time

import cairo

# gtk4-layer-shell interposes GTK's Wayland surface creation. It must be
# loaded *before* PyGObject loads GTK/GDK, otherwise init_for_window() can
# silently fail and Hyprland will see an ordinary tiled application window.
from ctypes import CDLL
from ctypes.util import find_library

_layer_shell_library = find_library("gtk4-layer-shell") or "libgtk4-layer-shell.so.0"
CDLL(_layer_shell_library)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell as LayerShell

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

LAYER_MAP = {
    "background": LayerShell.Layer.BACKGROUND,
    "bottom": LayerShell.Layer.BOTTOM,
    "top": LayerShell.Layer.TOP,
    "overlay": LayerShell.Layer.OVERLAY,
}

_parser = argparse.ArgumentParser(
    description="Real-time audio visualizer for Hyprland (cava + GTK4 layer-shell)",
)
_parser.add_argument("--config", type=str, default=None,
                     help="JSON configuration file (explicit CLI options override it)")
_parser.add_argument("--anchor", choices=("top", "bottom", "center"), default="center",
                     help="Vertical screen placement (default: center)")
_parser.add_argument("--width", type=int, default=0,
                     help="Surface width in logical pixels, 0 = full monitor (default: 0)")
_parser.add_argument("--height", type=int, default=0,
                     help="Surface height in logical pixels, 0 = full monitor (default: 0)")
_parser.add_argument("--align", choices=("left", "center", "right"), default="center",
                     help="Horizontal screen alignment (default: center)")
_parser.add_argument("--direction", choices=("up", "down"), default="up",
                     help="Direction bars extend (default: up)")
_parser.add_argument("--style", choices=("wave", "bars"), default="wave",
                     help="Smooth waveform or discrete columns (default: wave)")
_parser.add_argument("--bar-gap", type=float, default=3.0,
                     help="Gap between discrete bars in logical pixels (default: 3)")
_parser.add_argument("--reactivity", type=float, default=1.0,
                     help="Global amplitude multiplier (default: 1.0)")
_parser.add_argument("--wave-fill", action=argparse.BooleanOptionalAction, default=True,
                     help="Fill area under waveform (default: true; --no-wave-fill for outline)")
_parser.add_argument("--wave-glow", action=argparse.BooleanOptionalAction, default=False,
                     help="Add a subtle soft glow behind the wave contour")
_parser.add_argument("--glow-width", type=float, default=9.0,
                     help="Approximate glow radius in logical pixels (default: 9)")
_parser.add_argument("--glow-opacity", type=float, default=0.22,
                     help="Maximum glow alpha, 0..1 (default: 0.22)")
_parser.add_argument("--bars", type=int, default=50, help="Number of bars (default: 50)")
_parser.add_argument("--framerate", type=int, default=0, help="Framerate, 0 = auto-detect (default: 0)")
_parser.add_argument("--height-pct", type=float, default=20, help="Height as %% of monitor (default: 20)")
_parser.add_argument("--opacity", type=float, default=0.55, help="Bar opacity 0-1 (default: 0.55)")
_parser.add_argument("--mirror", action="store_true", default=True, help="Mirror bars vertically (default: true)")
_parser.add_argument("--no-mirror", action="store_true", help="Disable vertical mirroring")
_parser.add_argument("--monstercat", action="store_true", default=True, help="Monstercat smoothing (default: true)")
_parser.add_argument("--no-monstercat", action="store_true", help="Disable monstercat smoothing")
_parser.add_argument("--noise-reduction", type=float, default=0.50, help="Noise reduction 0-1 (default: 0.50)")
_parser.add_argument("--sensitivity", type=int, default=150, help="Cava sensitivity (default: 150)")
_parser.add_argument("--gradient-speed", type=float, default=0.15, help="Gradient animation speed in cycles/sec (default: 0.15)")
_parser.add_argument("--layer", type=str, default="background", choices=LAYER_MAP.keys(),
                      help="Wayland layer (default: background)")
_parser.add_argument("--color-primary", type=str, default="#F2BF6E",
                      help='Primary gradient color as "#RRGGBB" (default: #F2BF6E)')
_parser.add_argument("--color-secondary", type=str, default="#E6BDC0",
                      help='Secondary gradient color as "#RRGGBB" (default: #E6BDC0)')
_parser.add_argument("--color-tertiary", type=str, default="#B5CF9E",
                      help='Tertiary gradient color as "#RRGGBB" (default: #B5CF9E)')
_parser.add_argument("--colors-file", type=str, default=None,
                      help="JSON palette or legacy Hyprland colors file (overrides --color-* flags)")
_parser.add_argument("--cava-config-dir", type=str, default=None,
                      help="Directory for generated cava config (default: $XDG_RUNTIME_DIR or /tmp)")
# Fade
_parser.add_argument("--fade", action="store_true", default=False,
                      help="Fade in/out on silence (default: off)")
_parser.add_argument("--no-fade", action="store_true", help="Disable fade (explicit)")
_parser.add_argument("--fade-in-speed", type=float, default=3.0,
                      help="Fade-in speed in opacity units/sec (default: 3.0)")
_parser.add_argument("--fade-out-speed", type=float, default=1.5,
                      help="Fade-out speed in opacity units/sec (default: 1.5)")
_parser.add_argument("--silence-threshold", type=float, default=0.02,
                      help="Peak level below which audio is considered silent (default: 0.02)")
# Saturation boost
_parser.add_argument("--boost-saturation", type=float, default=0.35,
                      help="Saturation boost at center, 0 to disable (default: 0.35)")
# Power-aware framerate
_parser.add_argument("--power-aware", action="store_true", default=False,
                      help="Scale framerate by power profile (default: off)")
_parser.add_argument("--no-power-aware", action="store_true", help="Disable power-aware scaling (explicit)")

def parse_settings():
    # Parse the path first, so the JSON file becomes the defaults for every CLI flag.
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--config")
    known, _ = preliminary.parse_known_args()
    if known.config:
        try:
            with open(os.path.expanduser(known.config), encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, ValueError) as exc:
            _parser.error(f"could not read JSON config: {exc}")
        if not isinstance(settings, dict):
            _parser.error("JSON config must contain an object")

        actions = {a.dest: a for a in _parser._actions if a.dest != "help"}
        defaults = {}
        quiet_zone = settings.pop("quiet_zone", None)
        if quiet_zone is not None:
            if not isinstance(quiet_zone, dict):
                _parser.error("quiet_zone must be a JSON object")
            if set(quiet_zone) - {"start", "end", "reactivity", "smoothing", "transition", "profile"}:
                _parser.error("quiet_zone has unknown keys")
            quiet_zone = {**{"start": 0.3, "end": 0.7, "reactivity": 0.25,
                             "smoothing": 0.85, "transition": 0.0, "profile": "valley"}, **quiet_zone}
            for k, v in quiet_zone.items():
                if k == "profile":
                    if v not in ("valley", "plateau"):
                        _parser.error("quiet_zone.profile must be 'valley' or 'plateau'")
                elif isinstance(v, bool) or not isinstance(v, (int, float)):
                    _parser.error(f"quiet_zone.{k} must be numeric")
            if not (0 <= quiet_zone["start"] < quiet_zone["end"] <= 1):
                _parser.error("quiet_zone must satisfy 0 <= start < end <= 1")
            if not (quiet_zone["reactivity"] >= 0 and 0 <= quiet_zone["smoothing"] < 1
                    and 0 <= quiet_zone["transition"] <= 0.5):
                _parser.error("invalid quiet_zone reactivity/smoothing/transition")
        defaults["quiet_zone"] = quiet_zone

        edge_fade = settings.pop("edge_fade", None)
        if edge_fade is not None:
            if not isinstance(edge_fade, dict):
                _parser.error("edge_fade must be a JSON object")
            if set(edge_fade) - {"width", "reactivity", "smoothing"}:
                _parser.error("edge_fade has unknown keys")
            edge_fade = {**{"width": 0.18, "reactivity": 0.0, "smoothing": 0.65}, **edge_fade}
            for k, v in edge_fade.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    _parser.error(f"edge_fade.{k} must be numeric")
            if not (0 < edge_fade["width"] <= 0.5 and 0 <= edge_fade["reactivity"] <= 1
                    and 0 <= edge_fade["smoothing"] < 1):
                _parser.error("edge_fade width must be in (0, 0.5], reactivity in [0, 1], smoothing in [0, 1)")
        defaults["edge_fade"] = edge_fade
        for raw_key, value in settings.items():
            key = raw_key.replace("-", "_")
            action = actions.get(key)
            if action is None or key in ("config", "no_mirror", "no_monstercat", "no_fade", "no_power_aware"):
                _parser.error(f"unknown/unsupported config option: {raw_key}")
            if isinstance(action, argparse.BooleanOptionalAction) or isinstance(action, argparse._StoreTrueAction):
                if not isinstance(value, bool):
                    _parser.error(f"config option {raw_key} must be boolean")
            elif action.type in (int, float):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    _parser.error(f"config option {raw_key} must be numeric")
                if action.type == int and int(value) != value:
                    _parser.error(f"config option {raw_key} must be an integer")
                value = action.type(value)
            elif action.type is str or action.type is None:
                if not isinstance(value, str) and value is not None:
                    _parser.error(f"config option {raw_key} must be a string")
            if action.choices is not None and value not in action.choices:
                _parser.error(f"invalid value for {raw_key}: {value!r}; choose from {list(action.choices)}")
            if key == "colors_file" and value is not None and not os.path.isabs(os.path.expanduser(value)):
                value = os.path.join(os.path.dirname(os.path.abspath(os.path.expanduser(known.config))), value)
            defaults[key] = value
        _parser.set_defaults(**defaults)

    parsed = _parser.parse_args()
    if parsed.bars < 1 or parsed.width < 0 or parsed.height < 0 or parsed.bar_gap < 0:
        _parser.error("bars must be >= 1; width, height, and bar-gap must be >= 0")
    if parsed.reactivity < 0:
        _parser.error("reactivity must be >= 0")
    if parsed.glow_width < 0 or not 0 <= parsed.glow_opacity <= 1:
        _parser.error("glow-width must be >= 0 and glow-opacity must be 0..1")
    if not 0 <= parsed.opacity <= 1 or not 0 < parsed.height_pct <= 100:
        _parser.error("opacity must be 0..1 and height-pct must be > 0 and <= 100")
    return parsed


args = parse_settings()
if not hasattr(args, "quiet_zone"):
    args.quiet_zone = None
if not hasattr(args, "edge_fade"):
    args.edge_fade = None

if args.no_mirror:
    args.mirror = False
if args.no_monstercat:
    args.monstercat = False
if args.no_fade:
    args.fade = False
if args.no_power_aware:
    args.power_aware = False

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------


def parse_hex_color(s):
    """Parse '#RRGGBB' or 'RRGGBB' into (r, g, b) floats 0-1."""
    s = s.strip().lstrip("#")
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"Invalid hex color: '{s}' (expected 6-digit hex like '#FF8800')")
    return (int(s[0:2], 16) / 255, int(s[2:4], 16) / 255, int(s[4:6], 16) / 255)


def parse_colors_file(path):
    """Read JSON {primary, secondary, tertiary}, or legacy Hyprland rgb() format."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    if path.lower().endswith(".json"):
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("colors JSON must contain an object")
        # A simple flat palette is easy to generate from Unclaimed Bloom.
        return {key: parse_hex_color(value) for key, value in data.items()
                if key in ("primary", "secondary", "tertiary")}
    colors = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("$") and "= rgb(" in line:
            name, _, val = line.partition("=")
            key = name.strip().lstrip("$")
            hex_str = val.strip().removeprefix("rgb(").removesuffix(")")
            if key in ("primary", "secondary", "tertiary"):
                colors[key] = parse_hex_color(hex_str)
    return colors


def interpolate_color(c1, c2, t):
    return (
        c1[0] + (c2[0] - c1[0]) * t,
        c1[1] + (c2[1] - c1[1]) * t,
        c1[2] + (c2[2] - c1[2]) * t,
    )


def rgb_to_hsl(r, g, b):
    cmax, cmin = max(r, g, b), min(r, g, b)
    delta = cmax - cmin
    l = (cmax + cmin) / 2
    if delta == 0:
        return 0, 0, l
    denom = 1 - abs(2 * l - 1)
    s = delta / denom if denom > 1e-7 else 1.0
    if cmax == r:
        h = ((g - b) / delta) % 6
    elif cmax == g:
        h = (b - r) / delta + 2
    else:
        h = (r - g) / delta + 4
    return h / 6, min(max(s, 0.0), 1.0), l


def hsl_to_rgb(h, s, l):
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h * 6) % 2 - 1))
    m = l - c / 2
    h6 = int(h * 6) % 6
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][h6]
    return (r + m, g + m, b + m)


def boost_saturation(rgb, amount=0.35):
    h, s, l = rgb_to_hsl(*rgb)
    s = min(1.0, s + amount)
    return hsl_to_rgb(h, s, l)


# ---------------------------------------------------------------------------
# Color state
# ---------------------------------------------------------------------------


def load_colors():
    """Load colors from file (if given) or CLI hex values."""
    if args.colors_file:
        path = os.path.expanduser(args.colors_file)
        c = parse_colors_file(path)
        primary = c.get("primary", parse_hex_color(args.color_primary))
        secondary = c.get("secondary", parse_hex_color(args.color_secondary))
        tertiary = c.get("tertiary", parse_hex_color(args.color_tertiary))
        return primary, secondary, tertiary
    return (
        parse_hex_color(args.color_primary),
        parse_hex_color(args.color_secondary),
        parse_hex_color(args.color_tertiary),
    )


fg_primary, fg_secondary, fg_tertiary = load_colors()
# Pre-computed boosted colors (updated on color reload)
_boosted_primary = _boosted_secondary = _boosted_tertiary = (0, 0, 0)


def _recompute_boosted():
    global _boosted_primary, _boosted_secondary, _boosted_tertiary
    amt = args.boost_saturation
    if amt > 0:
        _boosted_primary = boost_saturation(fg_primary, amt)
        _boosted_secondary = boost_saturation(fg_secondary, amt)
        _boosted_tertiary = boost_saturation(fg_tertiary, amt)


def reload_colors():
    global fg_primary, fg_secondary, fg_tertiary
    try:
        colors = load_colors()
    except (OSError, ValueError, TypeError) as exc:
        print(f"Color reload failed: {exc}", file=sys.stderr)
        return False
    fg_primary, fg_secondary, fg_tertiary = colors
    _recompute_boosted()
    if drawing_area is not None:
        drawing_area.queue_draw()
    return False


_recompute_boosted()


# ---------------------------------------------------------------------------
# System detection
# ---------------------------------------------------------------------------


def detect_refresh_rate():
    """Read the active monitor's refresh rate from Hyprland IPC."""
    try:
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        sock_path = f"{runtime}/hypr/{sig}/.socket.sock"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(1)
            s.connect(sock_path)
            s.send(b"j/monitors")
            chunks = []
            while True:
                data = s.recv(8192)
                if not data:
                    break
                chunks.append(data)
        finally:
            s.close()
        monitors = json.loads(b"".join(chunks))
        for mon in (monitors or []):
            if mon.get("focused"):
                return int(mon.get("refreshRate", 60))
        if monitors:
            return int(monitors[0].get("refreshRate", 60))
    except Exception:
        pass
    return 60


def detect_power_profile():
    """Read active power profile via D-Bus (powerprofiles daemon)."""
    try:
        result = subprocess.run(
            ["busctl", "get-property", "net.hadess.PowerProfiles",
             "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles", "ActiveProfile"],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip().split('"')[1]
    except Exception:
        return "balanced"


def apply_power_scaling(refresh_rate):
    """Cap framerate based on power profile, scaled to monitor refresh rate."""
    if not args.power_aware:
        return max(30, refresh_rate // 2)
    profile = detect_power_profile()
    caps = {"power-saver": 30, "balanced": 45, "performance": 60}
    return caps.get(profile, 60)


if args.framerate <= 0:
    args.framerate = apply_power_scaling(detect_refresh_rate())

# ---------------------------------------------------------------------------
# Drawing state
# ---------------------------------------------------------------------------

bar_values = [0.0] * args.bars
# Per-bar EMA, maintained in rendering thread (Cava thread only writes bar_values).
_smoothed_values = [0.0] * args.bars
win = None
drawing_area = None
_gradient_phase = 0.0
_last_draw_time = 0.0
_win_opacity = 0.0 if args.fade else 1.0
_cava_dirty = False
_data_frame = False  # True when the current draw was triggered by new cava data
# Cached curve points — reused during fade-only frames to skip bezier recomputation
_cached_points_top = None
_cached_points_bot = None
_points_valid = False


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def smooth_curve(cr, points):
    """Draw a smooth curve through points using cubic bezier splines."""
    if len(points) < 2:
        return
    cr.move_to(*points[0])
    if len(points) == 2:
        cr.line_to(*points[1])
        return
    for i in range(1, len(points) - 1):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        cp1x = x0 + (x1 - x0) * 0.5
        cp1y = y0 + (y1 - y0) * 0.5
        cp2x = x1
        cp2y = y1
        cr.curve_to(cp1x, cp1y, cp2x, cp2y, (x1 + x2) * 0.5, (y1 + y2) * 0.5)
    cr.line_to(*points[-1])


def draw_wave_glow(cr, path):
    """Approximate a soft halo with a few translucent Cairo strokes.

    The halo follows the existing outline; no compositor blur or image filter.
    The primary palette color is used, so a theme reload updates the glow.
    """
    if not args.wave_glow or args.glow_width <= 0 or args.glow_opacity <= 0:
        return
    alpha = args.glow_opacity * _win_opacity
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    # Wide-to-narrow translucent strokes mimic a small Gaussian halo.
    for scale, strength in ((2.6, 0.10), (1.7, 0.16), (1.0, 0.26)):
        cr.new_path()
        cr.append_path(path)
        cr.set_line_width(max(0.1, args.glow_width * scale))
        cr.set_source_rgba(*fg_primary, min(1.0, alpha * strength))
        cr.stroke()
    cr.restore()


def draw_func(_area, cr, width, height):
    global _gradient_phase, _last_draw_time, _win_opacity, _smoothed_values

    now = time.monotonic()
    dt = now - _last_draw_time if _last_draw_time > 0 else 0
    _last_draw_time = now
    _gradient_phase = (_gradient_phase + args.gradient_speed * dt) % 1.0

    # Fade window based on audio level
    if args.fade:
        peak = max(bar_values) if bar_values else 0.0
        if peak > args.silence_threshold:
            _win_opacity = min(1.0, _win_opacity + args.fade_in_speed * dt)
        else:
            _win_opacity = max(0.0, _win_opacity - args.fade_out_speed * dt)

        if _win_opacity < 0.005:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            return

    n = len(bar_values)
    if len(_smoothed_values) != n:
        _smoothed_values = [0.0] * n
    # Smoothly vary reactivity and temporal damping across horizontal position.
    # A smoothstep falloff at the two ends prevents visually cut-off waveforms.
    def smoothstep(t):
        t = max(0.0, min(1.0, t))
        return t * t * (3.0 - 2.0 * t)

    rendered_values = []
    zone = args.quiet_zone
    edge = args.edge_fade
    for i, val in enumerate(bar_values):
        # Use exact endpoints for the first/last bar; makes both tips taper to zero.
        position = i / max(1, n - 1)
        multiplier = args.reactivity
        hold = 0.0

        if zone is not None:
            if zone["profile"] == "valley":
                # Single continuous dip: quietest at the center, gradually
                # returning to full amplitude at either boundary. No flat
                # low-amplitude rectangle or hard cutoff in either style.
                midpoint = (zone["start"] + zone["end"]) / 2.0
                if position <= midpoint:
                    zone_weight = smoothstep(
                        (position - zone["start"]) / max(1e-9, midpoint - zone["start"])
                    )
                else:
                    zone_weight = smoothstep(
                        (zone["end"] - position) / max(1e-9, zone["end"] - midpoint)
                    )
            else:
                # Legacy flat quiet zone, with optional soft shoulders.
                transition = zone["transition"]
                if transition > 0:
                    enter = smoothstep((position - (zone["start"] - transition)) / transition)
                    leave = smoothstep(((zone["end"] + transition) - position) / transition)
                    zone_weight = min(enter, leave)
                else:
                    zone_weight = float(zone["start"] <= position <= zone["end"])
            multiplier *= 1.0 + (zone["reactivity"] - 1.0) * zone_weight
            hold = max(hold, zone["smoothing"] * zone_weight)

        if edge is not None:
            distance = min(position, 1.0 - position)
            edge_weight = 1.0 - smoothstep(distance / edge["width"])
            multiplier *= 1.0 + (edge["reactivity"] - 1.0) * edge_weight
            hold = max(hold, edge["smoothing"] * edge_weight)

        target = min(1.0, max(0.0, val * multiplier))
        retention = hold ** (max(0.0, min(dt, 0.25)) * 60) if hold else 0.0
        smoothed = _smoothed_values[i] * retention + target * (1.0 - retention)
        _smoothed_values[i] = smoothed
        rendered_values.append(smoothed)
    if n == 0:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        return

    max_h = height * (args.height_pct / 100)
    center_y = height / 2

    # Clear the complete transparent drawing surface on every redraw.
    # This avoids ghosting when switching between bar heights.
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)

    global _cached_points_top, _cached_points_bot, _points_valid
    if args.style == "wave":
        # Preserve original vertically centered waveform when using defaults.
        # For the top/bottom placement, the curve starts at the selected edge.
        if args.anchor == "top":
            baseline = 0
            tip_sign = 1
        elif args.anchor == "bottom":
            baseline = height
            tip_sign = -1
        else:
            baseline = center_y if args.mirror else (center_y + max_h * 0.5 if args.direction == "up" else center_y - max_h * 0.5)
            tip_sign = -1 if args.direction == "up" else 1

        if _points_valid and _cached_points_top is not None and not _data_frame and not args.quiet_zone and not args.edge_fade:
            points_top = _cached_points_top
            points_bot = _cached_points_bot
        else:
            step = width / max(1, n - 1)
            points_top = []
            points_bot = []
            for i, val in enumerate(rendered_values):
                x = i * step
                extent = val * max_h * (0.5 if args.mirror else 1)
                points_top.append((x, baseline + tip_sign * extent))
                points_bot.append((x, baseline - tip_sign * extent))
            _cached_points_top = points_top
            _cached_points_bot = points_bot
            _points_valid = True
    else:
        _points_valid = False

    # 3-color sliding gradient
    span = width * 1.5
    gx0 = -span + _gradient_phase * span
    gx1 = gx0 + span

    oa = args.opacity * _win_opacity

    gradient = cairo.LinearGradient(gx0, 0, gx1, 0)
    gradient.add_color_stop_rgba(0.00, *fg_primary, oa)
    gradient.add_color_stop_rgba(0.33, *fg_secondary, oa)
    gradient.add_color_stop_rgba(0.66, *fg_tertiary, oa)
    gradient.add_color_stop_rgba(1.00, *fg_primary, oa)
    gradient.set_extend(cairo.Extend.REPEAT)

    lo = min(1.0, args.opacity * 2) * _win_opacity
    line_gradient = cairo.LinearGradient(gx0, 0, gx1, 0)
    line_gradient.add_color_stop_rgba(0.00, *fg_primary, lo)
    line_gradient.add_color_stop_rgba(0.33, *fg_secondary, lo)
    line_gradient.add_color_stop_rgba(0.66, *fg_tertiary, lo)
    line_gradient.add_color_stop_rgba(1.00, *fg_primary, lo)
    line_gradient.set_extend(cairo.Extend.REPEAT)

    # Saturation boost near center (uses pre-computed boosted colors)
    boost_amt = args.boost_saturation
    if boost_amt > 0:
        boost_h = max_h * 0.25
        bp = _boosted_primary
        bs = _boosted_secondary
        bt = _boosted_tertiary

        def make_gradient(y_from_center):
            dist = abs(y_from_center) / max(1, boost_h)
            if dist >= 1.0:
                return gradient
            t = 1.0 - dist
            o = (args.opacity + (args.opacity * 0.5) * t) * _win_opacity
            g = cairo.LinearGradient(gx0, 0, gx1, 0)
            g.add_color_stop_rgba(0.00, *interpolate_color(fg_primary, bp, t), o)
            g.add_color_stop_rgba(0.33, *interpolate_color(fg_secondary, bs, t), o)
            g.add_color_stop_rgba(0.66, *interpolate_color(fg_tertiary, bt, t), o)
            g.add_color_stop_rgba(1.00, *interpolate_color(fg_primary, bp, t), o)
            g.set_extend(cairo.Extend.REPEAT)
            return g

        fill_top = make_gradient(-max_h * 0.15)
        fill_bot = make_gradient(max_h * 0.15)
    else:
        fill_top = gradient
        fill_bot = gradient

    if args.style == "bars":
        bar_width = width / n
        gap = min(args.bar_gap, bar_width * 0.9)
        if args.anchor == "top":
            base_y, direction = 0, 1
        elif args.anchor == "bottom":
            base_y, direction = height, -1
        else:
            base_y = height / 2
            direction = -1 if args.direction == "up" else 1
        cr.set_source(gradient)
        for i, value in enumerate(rendered_values):
            extent = value * max_h * (0.5 if args.mirror and args.anchor == "center" else 1)
            x = i * bar_width + gap / 2
            y = base_y if direction > 0 else base_y - extent
            cr.rectangle(x, y, max(0.1, bar_width - gap), extent)
            if args.mirror and args.anchor == "center":
                other_y = base_y - extent if direction > 0 else base_y
                cr.rectangle(x, other_y, max(0.1, bar_width - gap), extent)
        cr.fill()
    else:
        cr.new_path()
        smooth_curve(cr, points_top)
        stroke_top = cr.copy_path()
        if args.wave_fill:
            cr.line_to(width, baseline)
            cr.line_to(0, baseline)
            cr.close_path()
            cr.set_source(fill_top)
            cr.fill()

        draw_wave_glow(cr, stroke_top)
        cr.new_path()
        cr.append_path(stroke_top)
        cr.set_source(line_gradient)
        cr.set_line_width(2)
        cr.stroke()

        if args.mirror:
            cr.new_path()
            smooth_curve(cr, points_bot)
            stroke_bot = cr.copy_path()
            if args.wave_fill:
                cr.line_to(width, baseline)
                cr.line_to(0, baseline)
                cr.close_path()
                cr.set_source(fill_bot)
                cr.fill()

            draw_wave_glow(cr, stroke_bot)
            cr.new_path()
            cr.append_path(stroke_bot)
            cr.set_source(line_gradient)
            cr.set_line_width(2)
            cr.stroke()



# ---------------------------------------------------------------------------
# Cava integration
# ---------------------------------------------------------------------------


def build_cava_config():
    """Write a temporary cava config and return its path."""
    if args.cava_config_dir:
        config_dir = os.path.expanduser(args.cava_config_dir)
    else:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        config_dir = os.path.join(runtime, "hypr-cava-visualizer")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "cava.conf")
    config = f"""[general]
bars = {args.bars}
framerate = {args.framerate}
autosens = 1
sensitivity = {args.sensitivity}
monstercat = {1 if args.monstercat else 0}
noise_reduction = {args.noise_reduction}

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 1000
channels = mono
mono_option = average
"""
    with open(config_path, "w") as f:
        f.write(config)
    return config_path


_cava_proc = None


def cava_reader():
    """Read cava output in a background thread and update bar_values."""
    global bar_values, _cava_proc, _cava_dirty
    config_path = build_cava_config()
    proc = subprocess.Popen(
        ["cava", "-p", config_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    _cava_proc = proc
    try:
        fd = proc.stdout.fileno()
        os.set_blocking(fd, False)
        buf = b""
        while True:
            select.select([fd], [], [])
            # Drain all available data, keep only the last complete line
            while True:
                try:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        return
                    buf += chunk
                except BlockingIOError:
                    break
            if b"\n" not in buf:
                continue
            rest, _, trailing = buf.rpartition(b"\n")
            buf = trailing
            if b"\n" in rest:
                last_line = rest.rsplit(b"\n", 1)[1]
            else:
                last_line = rest
            vals = last_line.decode().strip().rstrip(";").split(";")
            try:
                bar_values = [min(1.0, int(v) / 1000) for v in vals if v]
            except ValueError:
                continue
            _cava_dirty = True
    except Exception:
        pass
    finally:
        proc.kill()
        proc.wait()


def _cleanup_cava():
    if _cava_proc and _cava_proc.poll() is None:
        _cava_proc.kill()
        _cava_proc.wait()


atexit.register(_cleanup_cava)


# ---------------------------------------------------------------------------
# Color file watcher
# ---------------------------------------------------------------------------


def color_reload_watcher():
    """Watch colors file for changes and reload on modification."""
    if not args.colors_file:
        return
    path = os.path.expanduser(args.colors_file)
    last_mtime = 0
    while True:
        try:
            mtime = os.path.getmtime(path)
            if mtime != last_mtime:
                last_mtime = mtime
                GLib.idle_add(reload_colors)
        except Exception:
            pass
        time.sleep(2)


# ---------------------------------------------------------------------------
# GTK application
# ---------------------------------------------------------------------------


def on_activate(app):
    global win, drawing_area

    if not LayerShell.is_supported():
        raise RuntimeError(
            "GTK4 Layer Shell is unavailable. Run under Hyprland/Wayland "
            "(try GDK_BACKEND=wayland) and verify gtk4-layer-shell is installed."
        )

    win = Gtk.ApplicationWindow(application=app)
    win.add_css_class("cava-transparent")

    LayerShell.init_for_window(win)
    if not LayerShell.is_layer_window(win):
        raise RuntimeError(
            "Failed to initialize GtkWindow as a layer surface; "
            "gtk4-layer-shell must be loaded before GTK."
        )
    LayerShell.set_layer(win, LAYER_MAP[args.layer])
    # An unanchored axis is centered by layer-shell. Both anchored edges
    # stretch to the monitor in the original fullscreen mode.
    full_height = args.height == 0
    full_width = args.width == 0
    LayerShell.set_anchor(win, LayerShell.Edge.TOP,
                          full_height or args.anchor == "top")
    LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM,
                          full_height or args.anchor == "bottom")
    LayerShell.set_anchor(win, LayerShell.Edge.LEFT,
                          full_width or args.align == "left")
    LayerShell.set_anchor(win, LayerShell.Edge.RIGHT,
                          full_width or args.align == "right")
    if not full_width or not full_height:
        monitors = Gdk.Display.get_default().get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        geometry = monitor.get_geometry() if monitor else None
        surface_width = min(args.width, geometry.width) if geometry and args.width else (args.width or 1)
        surface_height = min(args.height, geometry.height) if geometry and args.height else (args.height or 1)
        win.set_default_size(surface_width, surface_height)
    LayerShell.set_exclusive_zone(win, -1)
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
    LayerShell.set_namespace(win, "hypr-cava-visualizer")

    drawing_area = Gtk.DrawingArea()
    drawing_area.set_vexpand(True)
    drawing_area.set_hexpand(True)
    drawing_area.set_draw_func(draw_func)
    win.set_child(drawing_area)

    # GTK themes may paint an opaque background before the Cairo draw callback.
    # Override theme CSS for this window only, using USER priority so the
    # transparent background wins over system/application theme declarations.
    css = Gtk.CssProvider()
    css.load_from_string("""
        window.cava-transparent,
        window.cava-transparent.background,
        window.cava-transparent > drawingarea {
            background-color: rgba(0, 0, 0, 0);
            background-image: none;
            box-shadow: none;
            backdrop-filter: none;
            border: none;
        }
    """)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER,
    )

    win.present()

    # Vblank-synced frame tick — fires once per compositor frame via the
    # frame clock, eliminating timer drift that causes perceived lag.
    _frame_skip = max(1, round(detect_refresh_rate() / args.framerate))
    _frame_counter = [0]

    def frame_tick(widget, clock):
        global _cava_dirty, _data_frame
        _frame_counter[0] += 1
        if _frame_counter[0] % _frame_skip != 0:
            return True
        fading = args.fade and 0.005 < _win_opacity < 0.995
        if (_cava_dirty or fading) and drawing_area:
            _data_frame = _cava_dirty
            _cava_dirty = False
            drawing_area.queue_draw()
        return True

    drawing_area.add_tick_callback(frame_tick)

    threading.Thread(target=cava_reader, daemon=True).start()
    threading.Thread(target=color_reload_watcher, daemon=True).start()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    app = Gtk.Application(application_id="dev.hypr.cava-visualizer")
    app.connect("activate", on_activate)
    app.run(None)


if __name__ == "__main__":
    main()
