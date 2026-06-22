"""Live per-core utilization window — like the Task Manager 'logical
processors' grid. Each CPU core gets its own small scrolling graph of its
utilization over time, plus a GPU utilization graph when a GPU is present.

Note: Linux exposes per-core CPU usage, but not per-shader-core GPU usage,
so the GPU is shown as one overall-utilisation graph + VRAM.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

import cairo

from .monitor import SystemStats
from .panel_base import CaretPanel

_COLS = 2          # cores laid out in this many columns
_WINDOWS = [("1 min", 60), ("5 min", 300), ("15 min", 900), ("All", None)]


class _CoreGraph(Gtk.DrawingArea):
    """An area-graph of one value's history (a list of (ts, pct)) over a
    selectable time window, with gaps left blank where the machine was off."""

    def __init__(self, width=150, height=40):
        super().__init__()
        self._series = []     # (ts, val)
        self._window = 300
        self.set_size_request(width, height)
        self.connect("draw", self._draw)

    def set_series(self, series, window):
        self._series = series
        self._window = window
        self.queue_draw()

    def _draw(self, _w, cr):
        a = self.get_allocation()
        w, h = a.width, a.height
        cr.set_source_rgba(0.97, 0.97, 0.97, 1.0)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgba(0.86, 0.86, 0.86, 1.0)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()

        s = self._series
        if len(s) < 2:
            return
        t1 = s[-1][0]
        t0 = (t1 - self._window) if self._window else s[0][0]
        span = max(1e-6, t1 - t0)
        pts = [(t, v) for (t, v) in s if t >= t0]
        if len(pts) < 2:
            return
        dts = sorted(pts[i][0] - pts[i - 1][0] for i in range(1, len(pts)))
        median = dts[len(dts) // 2] or 1.0
        gap = max(15.0, 6.0 * median)

        def x_of(t):
            return (t - t0) / span * w

        def y_of(v):
            return (h - 1) - (max(0.0, min(v, 100.0)) / 100.0) * (h - 2)

        # Line with gap breaks
        cr.set_source_rgba(0.24, 0.36, 0.60, 0.95)
        cr.set_line_width(1.3)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        prev_t = None
        for t, v in pts:
            if prev_t is None or t - prev_t > gap:
                cr.move_to(x_of(t), y_of(v))
            else:
                cr.line_to(x_of(t), y_of(v))
            prev_t = t
        cr.stroke()


class _GraphCell(Gtk.Box):
    def __init__(self, title):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._title = title
        self.label = Gtk.Label(xalign=0.0)
        self.label.set_markup(f"<small><b>{title}</b></small>")
        self.graph = _CoreGraph()
        self.graph.set_hexpand(True)
        self.pack_start(self.label, False, False, 0)
        self.pack_start(self.graph, True, True, 0)

    def update(self, series, window, pct, suffix=""):
        self.graph.set_series(series, window)
        text = f"{self._title}   {pct:.0f}%"
        if suffix:
            text += f"   {suffix}"
        self.label.set_markup(f"<small><b>{text}</b></small>")


class CoresPanel(CaretPanel):
    def __init__(self):
        super().__init__("CPU / GPU cores", show_back=True)
        self.autohide = False
        self._hist = []
        root = self.body

        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ctrl.pack_start(Gtk.Label(label="History:"), False, False, 0)
        self._combo = Gtk.ComboBoxText()
        for label, secs in _WINDOWS:
            self._combo.append("none" if secs is None else str(secs), label)
        self._combo.set_active(1)   # 5 min
        self._combo.connect("changed", lambda *_: self._redraw())
        ctrl.pack_start(self._combo, False, False, 0)
        self._span_lbl = Gtk.Label(xalign=1.0)
        self._span_lbl.set_markup("<small>—</small>")
        ctrl.pack_end(self._span_lbl, True, True, 0)
        root.pack_start(ctrl, False, False, 2)

        self._cpu_header = Gtk.Label(xalign=0)
        self._cpu_header.set_markup("<b>CPU cores</b>")
        root.pack_start(self._cpu_header, False, False, 2)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(320)
        scroll.set_max_content_height(380)
        root.pack_start(scroll, True, True, 0)

        self._cpu_grid = Gtk.Grid()
        self._cpu_grid.set_column_spacing(12)
        self._cpu_grid.set_row_spacing(8)
        self._cpu_grid.set_column_homogeneous(True)
        scroll.add(self._cpu_grid)
        self._core_cells = []

        # GPU
        self._gpu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._gpu_box.set_no_show_all(True)
        root.pack_start(self._gpu_box, False, False, 0)
        gpu_header = Gtk.Label(xalign=0)
        gpu_header.set_markup("<b>GPU</b>")
        self._gpu_box.pack_start(gpu_header, False, False, 0)
        self._gpu_cell = _GraphCell("Usage")
        self._gpu_box.pack_start(self._gpu_cell, False, False, 0)

    def _ensure_cores(self, n):
        if len(self._core_cells) == n:
            return
        for c in self._cpu_grid.get_children():
            self._cpu_grid.remove(c)
        self._core_cells = []
        for i in range(n):
            cell = _GraphCell(f"Core {i}")
            self._cpu_grid.attach(cell, i % _COLS, i // _COLS, 1, 1)
            self._core_cells.append(cell)
        self._cpu_grid.show_all()

    def _window(self):
        wid = self._combo.get_active_id()
        return None if wid == "none" else int(wid)

    def update(self, s: SystemStats, hist=None):
        self._hist = hist or []
        self._last_s = s
        self._redraw()

    def _redraw(self):
        s = getattr(self, "_last_s", None)
        hist = self._hist
        if s is None:
            return
        cores = s.cpu_per_core or []
        self._ensure_cores(len(cores))
        self._cpu_header.set_markup(f"<b>CPU — {len(cores)} cores</b>")
        window = self._window()

        for i, cell in enumerate(self._core_cells):
            series = [(t, c[i]) for (t, c, _g) in hist if i < len(c)]
            cur = cores[i] if i < len(cores) else 0.0
            cell.update(series, window, cur)

        if s.gpu_available:
            self._gpu_box.set_visible(True)
            suffix = ""
            if s.gpu_mem_total_mb > 0:
                suffix = (f"VRAM {s.gpu_mem_used_mb/1024:.1f}/"
                          f"{s.gpu_mem_total_mb/1024:.1f}G")
            if s.gpu_temp > 0:
                suffix += (f"  {s.gpu_temp:.0f}°C" if suffix else f"{s.gpu_temp:.0f}°C")
            gseries = [(t, g) for (t, _c, g) in hist if g is not None]
            self._gpu_cell.update(gseries, window, s.gpu_percent, suffix)
        else:
            self._gpu_box.set_visible(False)

        # Time-span label so it's clear how much is shown.
        if len(hist) >= 2:
            actual = hist[-1][0] - hist[0][0]
            shown = actual if window is None else min(window, actual)
            self._span_lbl.set_markup(
                f"<small>showing last {_fmt_span(shown)}</small>")
        else:
            self._span_lbl.set_markup("<small>collecting…</small>")


def _fmt_span(sec):
    sec = int(sec)
    if sec >= 3600:
        return f"{sec/3600:.1f} h"
    if sec >= 60:
        return f"{sec//60} min"
    return f"{sec} s"

