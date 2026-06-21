"""Live per-core utilization window — like the Task Manager 'logical
processors' grid. Shows every CPU core's usage in real time, plus a GPU
section (overall utilisation + VRAM) when a GPU is present.

Note: Linux exposes per-core CPU usage, but not per-shader-core GPU usage,
so the GPU is shown as overall utilisation + memory rather than per-core.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .monitor import SystemStats

_COLS = 2  # cores laid out in this many columns


class CoresWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="SysMon — Cores")
        self.set_default_size(440, 340)
        self.connect("delete-event", self._on_close)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_start(14)
        root.set_margin_end(14)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        self.add(root)

        # ── CPU ────────────────────────────────────────────────────────
        self._cpu_header = Gtk.Label(xalign=0)
        self._cpu_header.set_markup("<b>CPU cores</b>")
        root.pack_start(self._cpu_header, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        root.pack_start(scroll, True, True, 0)

        self._cpu_grid = Gtk.Grid()
        self._cpu_grid.set_column_spacing(12)
        self._cpu_grid.set_row_spacing(5)
        scroll.add(self._cpu_grid)
        self._core_bars = []
        self._core_vals = []

        # ── GPU ────────────────────────────────────────────────────────
        self._gpu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._gpu_box.set_no_show_all(True)
        root.pack_start(self._gpu_box, False, False, 0)

        gpu_header = Gtk.Label(xalign=0)
        gpu_header.set_markup("<b>GPU</b>")
        self._gpu_box.pack_start(gpu_header, False, False, 0)

        self._gpu_util_bar, self._gpu_util_val = self._labeled_bar(
            self._gpu_box, "Usage")
        self._gpu_vram_bar, self._gpu_vram_val = self._labeled_bar(
            self._gpu_box, "VRAM")

    # ── Helpers ────────────────────────────────────────────────────────────

    def _labeled_bar(self, box, name):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=name, xalign=0)
        lbl.set_width_chars(7)
        bar = Gtk.ProgressBar()
        bar.set_hexpand(True)
        bar.set_valign(Gtk.Align.CENTER)
        val = Gtk.Label(label="0%", xalign=1)
        val.set_width_chars(5)
        row.pack_start(lbl, False, False, 0)
        row.pack_start(bar, True, True, 0)
        row.pack_end(val, False, False, 0)
        box.pack_start(row, False, False, 0)
        return bar, val

    def _ensure_cores(self, n):
        if len(self._core_bars) == n:
            return
        for c in self._cpu_grid.get_children():
            self._cpu_grid.remove(c)
        self._core_bars = []
        self._core_vals = []
        for i in range(n):
            r = i // _COLS
            c = (i % _COLS) * 3
            lbl = Gtk.Label(label=f"Core {i}", xalign=0)
            lbl.set_width_chars(7)
            bar = Gtk.ProgressBar()
            bar.set_hexpand(True)
            bar.set_valign(Gtk.Align.CENTER)
            val = Gtk.Label(label="0%", xalign=1)
            val.set_width_chars(5)
            self._cpu_grid.attach(lbl, c, r, 1, 1)
            self._cpu_grid.attach(bar, c + 1, r, 1, 1)
            self._cpu_grid.attach(val, c + 2, r, 1, 1)
            self._core_bars.append(bar)
            self._core_vals.append(val)
        self._cpu_grid.show_all()

    # ── Update ─────────────────────────────────────────────────────────────

    def update(self, s: SystemStats):
        cores = s.cpu_per_core or []
        self._ensure_cores(len(cores))
        self._cpu_header.set_markup(f"<b>CPU — {len(cores)} cores</b>")
        for i, v in enumerate(cores):
            self._core_bars[i].set_fraction(min(max(v, 0.0) / 100.0, 1.0))
            self._core_vals[i].set_text(f"{v:.0f}%")

        if s.gpu_available:
            self._gpu_box.set_visible(True)
            self._gpu_util_bar.set_fraction(min(s.gpu_percent / 100.0, 1.0))
            self._gpu_util_val.set_text(f"{s.gpu_percent:.0f}%")
            if s.gpu_mem_total_mb > 0:
                frac = s.gpu_mem_used_mb / s.gpu_mem_total_mb
                self._gpu_vram_bar.set_fraction(min(frac, 1.0))
                self._gpu_vram_val.set_text(
                    f"{s.gpu_mem_used_mb/1024:.1f}/{s.gpu_mem_total_mb/1024:.1f}G")
        else:
            self._gpu_box.set_visible(False)

    def present_window(self):
        self.show_all()
        self.present()

    def _on_close(self, *_):
        self.hide()
        return True   # keep the window alive, just hide it
