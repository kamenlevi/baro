"""Generate a single STATIC menu-bar icon (a simple bar-chart glyph).

The icon never changes — it does not reflect live usage, never moves and
never resizes. Live stats live in the dropdown panel instead.
"""
import os
import tempfile

import cairo

_ICON_DIR = os.path.join(tempfile.gettempdir(), "sysmon_icons")
os.makedirs(_ICON_DIR, exist_ok=True)
_PATH = [None]


def generate_tray_icon(*_args, size: int = 22, **_kwargs) -> str:
    """Return the path to the static icon, drawing it once and caching it.

    Extra args are accepted and ignored so existing callers keep working.
    """
    if _PATH[0] is not None and os.path.exists(_PATH[0]):
        return _PATH[0]

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    # Transparent background
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.paint()

    # Three static bars of fixed heights — a recognisable "stats" glyph.
    # White so it stays visible on the dark Ubuntu top bar.
    heights = [0.45, 0.85, 0.65]
    n = len(heights)
    pad = 3
    gap = 2
    bar_w = (size - pad * 2 - gap * (n - 1)) / n
    base_y = size - pad

    ctx.set_source_rgba(0.92, 0.92, 0.92, 1.0)
    for i, h in enumerate(heights):
        x = pad + i * (bar_w + gap)
        bar_h = h * (size - pad * 2)
        _rounded_rect(ctx, x, base_y - bar_h, bar_w, bar_h, 1.2)
        ctx.fill()

    path = os.path.join(_ICON_DIR, "sysmon_static.png")
    surface.write_to_png(path)
    _PATH[0] = path
    return path


def _rounded_rect(ctx, x, y, w, h, r):
    if h <= 0 or w <= 0:
        return
    r = min(r, w / 2, h / 2)
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -1.5708, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, 1.5708)
    ctx.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    ctx.arc(x + r, y + r, r, 3.1416, 4.7124)
    ctx.close_path()
