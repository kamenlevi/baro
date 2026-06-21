"""Dynamically generate the tray icon as a PNG using Cairo."""
import os
import tempfile

import cairo

_ICON_DIR = os.path.join(tempfile.gettempdir(), "sysmon_icons")
os.makedirs(_ICON_DIR, exist_ok=True)
_COUNTER = [0]
_LAST_KEY = [None]
_LAST_PATH = [None]


def _bar_color(pct: float):
    if pct >= 90:
        return (1.0, 0.22, 0.22)
    if pct >= 70:
        return (1.0, 0.68, 0.0)
    return (0.25, 0.72, 1.0)


def generate_tray_icon(
    cpu_pct: float,
    ram_pct: float,
    gpu_pct: float = 0.0,
    has_gpu: bool = False,
    has_warning: bool = False,
    size: int = 22,
) -> str:
    """Draw bars for cpu/gpu/ram. Returns path to written PNG."""
    # Skip rendering when the visible state (rounded percentages + flags) is
    # unchanged — this happens most ticks at idle and avoids a PNG write.
    key = (
        int(round(cpu_pct)),
        int(round(ram_pct)),
        int(round(gpu_pct)) if has_gpu else -1,
        bool(has_warning),
        size,
    )
    if _LAST_KEY[0] == key and _LAST_PATH[0] is not None and os.path.exists(_LAST_PATH[0]):
        return _LAST_PATH[0]

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    # Transparent background
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.paint()

    bars = [("cpu", cpu_pct), ("gpu", gpu_pct if has_gpu else None), ("ram", ram_pct)]

    n = len(bars)
    pad = 1
    gap = 1
    total_gap = gap * (n - 1) + pad * 2
    bar_w = max(2, (size - total_gap) // n)

    for i, (_, pct) in enumerate(bars):
        x = pad + i * (bar_w + gap)
        y_bg_top = pad
        bg_h = size - pad * 2

        # Dark background track (always drawn for constant width)
        ctx.set_source_rgba(0.15, 0.15, 0.15, 0.85)
        _rounded_rect(ctx, x, y_bg_top, bar_w, bg_h, 1)
        ctx.fill()

        if pct is None:
            continue

        # Filled portion (from bottom)
        bar_h = max(1, int(pct / 100.0 * bg_h))
        r, g, b = _bar_color(pct)
        ctx.set_source_rgba(r, g, b, 0.95)
        y_fill = pad + bg_h - bar_h
        _rounded_rect(ctx, x, y_fill, bar_w, bar_h, 1)
        ctx.fill()

    # Warning triangle overlay (top-right corner)
    if has_warning:
        tw = 8
        tx = size - tw - 0
        ty = 0
        ctx.set_source_rgba(1.0, 0.85, 0.0, 1.0)
        ctx.move_to(tx + tw / 2, ty)
        ctx.line_to(tx + tw, ty + tw)
        ctx.line_to(tx, ty + tw)
        ctx.close_path()
        ctx.fill()
        ctx.set_source_rgba(0.1, 0.1, 0.1, 1.0)
        ctx.set_line_width(1)
        ctx.move_to(tx + tw / 2, ty + 2.5)
        ctx.line_to(tx + tw / 2, ty + tw - 2.5)
        ctx.stroke()
        ctx.arc(tx + tw / 2, ty + tw - 1.5, 0.8, 0, 6.28)
        ctx.fill()

    # Alternate filenames so AppIndicator is forced to refresh
    _COUNTER[0] = (_COUNTER[0] + 1) % 2
    path = os.path.join(_ICON_DIR, f"sysmon_icon_{_COUNTER[0]}.png")
    surface.write_to_png(path)
    _LAST_KEY[0] = key
    _LAST_PATH[0] = path
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
