"""
Stats-style dropdown panel.

A single white rectangular window that drops down directly under the
menu-bar icon, shows the current system stats, and hides as soon as the
user clicks anywhere else (or presses Escape). There is no title bar, no
drag handle, no resize grip — and no other window is ever opened.

Layout per metric (modelled on the Stats macOS app):

    CPU                12%
    ====------------
    3.4 GHz · 55°C
"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

from .monitor import SystemStats

MIN_W = 270

CSS = b"""
window.sysmon-popup {
    background-color: #ffffff;
    border: 1px solid #d4d4d4;
}

.metric-name { color: #1a1a1a; font-size: 12px; font-weight: bold; }
.metric-val  { color: #1a1a1a; font-size: 12px; font-weight: bold; }
.metric-sub  { color: #8a8a8a; font-size: 10px; }
.warn-text   { color: #5a5a5a; font-size: 10px; }

progressbar trough {
    background-color: #ececec;
    border-radius: 2px;
    border: none;
    min-height: 6px;
}
progressbar progress {
    background-color: #585858;
    border-radius: 2px;
    min-height: 6px;
}

.foot-btn {
    background-color: #f2f2f2;
    color: #333333;
    border: 1px solid #d4d4d4;
    border-radius: 5px;
    padding: 3px 12px;
    font-size: 11px;
}
.foot-btn:hover { background-color: #e7e7e7; }

separator { background-color: #ececec; min-height: 1px; }
"""

_CSS_APPLIED = [False]


def _apply_css():
    if _CSS_APPLIED[0]:
        return
    p = Gtk.CssProvider()
    p.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _CSS_APPLIED[0] = True


def _lbl(text="", css="metric-sub", xalign=0.0, ellipsize=False) -> Gtk.Label:
    l = Gtk.Label(label=text, xalign=xalign)
    if css:
        l.get_style_context().add_class(css)
    if ellipsize:
        l.set_ellipsize(Pango.EllipsizeMode.END)
    return l


class _MetricBlock(Gtk.Box):
    """One metric: name + value header, a bar, and a sub-detail line."""

    def __init__(self, name: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.name_lbl = _lbl(name, "metric-name", xalign=0.0)
        self.val_lbl = _lbl("", "metric-val", xalign=1.0)
        head.pack_start(self.name_lbl, True, True, 0)
        head.pack_end(self.val_lbl, False, False, 0)
        self.pack_start(head, False, False, 0)

        self.bar = Gtk.ProgressBar()
        self.bar.set_hexpand(True)
        self.pack_start(self.bar, False, False, 0)

        self.sub_lbl = _lbl("", "metric-sub", xalign=0.0, ellipsize=True)
        self.pack_start(self.sub_lbl, False, False, 0)

    def set(self, pct: float, value_text: str, sub: str = ""):
        self.bar.set_fraction(min(max(pct, 0.0) / 100.0, 1.0))
        self.val_lbl.set_text(value_text)
        self.sub_lbl.set_text(sub)
        self.sub_lbl.set_visible(bool(sub))


class PopupWindow(Gtk.Window):

    def __init__(self, on_open_app, settings, on_settings=None, on_quit=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_open_app = on_open_app
        self.settings = settings
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._fan_controller = None
        self._shown_at = 0.0

        _apply_css()

        self.get_style_context().add_class("sysmon-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_size_request(MIN_W, -1)

        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key_press)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_start(14)
        root.set_margin_end(14)
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        self.add(root)

        # ── Metric blocks ──────────────────────────────────────────────
        self._cpu = _MetricBlock("CPU")
        root.pack_start(self._cpu, False, False, 0)

        self._gpu = _MetricBlock("GPU")
        self._gpu.set_no_show_all(True)
        root.pack_start(self._gpu, False, False, 0)

        self._ram = _MetricBlock("Memory")
        root.pack_start(self._ram, False, False, 0)

        # ── Fans (read-only RPM rows) ──────────────────────────────────
        self._fan_sep = Gtk.Separator()
        self._fan_sep.set_no_show_all(True)
        root.pack_start(self._fan_sep, False, False, 0)

        self._fan_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._fan_box.set_margin_top(4)
        self._fan_box.set_no_show_all(True)
        root.pack_start(self._fan_box, False, False, 0)

        # ── Warnings ───────────────────────────────────────────────────
        self._warn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self._warn_box.set_margin_top(4)
        self._warn_box.set_no_show_all(True)
        root.pack_start(self._warn_box, False, False, 0)

        # ── Footer: Settings + Quit ────────────────────────────────────
        root.pack_start(Gtk.Separator(), False, False, 0)
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        foot.set_margin_top(8)

        settings_btn = Gtk.Button(label="Settings")
        settings_btn.get_style_context().add_class("foot-btn")
        settings_btn.connect("clicked", self._on_settings_clicked)
        foot.pack_start(settings_btn, False, False, 0)

        quit_btn = Gtk.Button(label="Quit")
        quit_btn.get_style_context().add_class("foot-btn")
        quit_btn.connect("clicked", lambda *_: self._on_quit() if self._on_quit else None)
        foot.pack_end(quit_btn, False, False, 0)

        root.pack_start(foot, False, False, 0)

        self.show_all()
        self.hide()

    # ── Auto-hide behaviour ────────────────────────────────────────────

    def _on_focus_out(self, *_):
        # Ignore the brief focus flicker as the indicator menu closes while
        # the panel opens; only auto-hide once it has settled.
        if time.monotonic() - self._shown_at < 0.4:
            return False
        self.hide()
        return False

    def _on_key_press(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
        return False

    def _on_settings_clicked(self, *_):
        self.hide()
        if self._on_settings:
            self._on_settings()

    # ── Data update ────────────────────────────────────────────────────

    def update(self, s: SystemStats):
        cfg = self.settings

        # CPU
        sub = ""
        if s.cpu_freq_mhz > 0:
            sub = f"{s.cpu_freq_mhz/1000:.1f} GHz"
        if cfg.show_temp and s.cpu_temp > 0:
            sub += (" · " if sub else "") + f"{s.cpu_temp:.0f}°C"
        if s.thermal_throttling:
            sub += " · throttling"
        self._cpu.set(s.cpu_percent, f"{s.cpu_percent:.0f}%", sub)

        # GPU
        if cfg.show_gpu and s.gpu_available:
            self._gpu.set_visible(True)
            sub = ""
            if s.gpu_mem_total_mb > 0:
                sub = f"{s.gpu_mem_used_mb/1024:.1f} / {s.gpu_mem_total_mb/1024:.1f} GB"
            if cfg.show_temp and s.gpu_temp > 0:
                sub += (" · " if sub else "") + f"{s.gpu_temp:.0f}°C"
            self._gpu.set(s.gpu_percent, f"{s.gpu_percent:.0f}%", sub)
        else:
            self._gpu.set_visible(False)

        # Memory
        sub = f"{s.ram_used_gb:.1f} / {s.ram_total_gb:.1f} GB"
        if s.swap_total_gb > 0:
            sub += f" · swap {s.swap_used_gb:.1f} / {s.swap_total_gb:.1f} GB"
        self._ram.set(s.ram_percent, f"{s.ram_percent:.0f}%", sub)

        # Fans
        self._update_fans(s.fans)

        # Warnings
        for c in self._warn_box.get_children():
            self._warn_box.remove(c)
        if s.warnings:
            self._warn_box.set_visible(True)
            for w in s.warnings:
                lbl = _lbl(f"!  {w}", "warn-text", xalign=0.0, ellipsize=True)
                self._warn_box.pack_start(lbl, False, False, 0)
                lbl.show()
        else:
            self._warn_box.set_visible(False)

    def _update_fans(self, fans):
        if not fans:
            self._fan_sep.set_visible(False)
            self._fan_box.set_visible(False)
            return

        self._fan_sep.set_visible(True)
        self._fan_box.set_visible(True)

        existing = self._fan_box.get_children()
        if len(existing) != len(fans):
            for c in existing:
                self._fan_box.remove(c)
            for label, rpm, _ctrl in fans:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                name = _lbl(label, "metric-sub", xalign=0.0, ellipsize=True)
                val = _lbl(f"{rpm} RPM", "metric-sub", xalign=1.0)
                row.pack_start(name, True, True, 0)
                row.pack_end(val, False, False, 0)
                self._fan_box.pack_start(row, False, False, 0)
                row.show_all()
        else:
            for row, (_label, rpm, _ctrl) in zip(existing, fans):
                children = row.get_children()
                if len(children) == 2:
                    children[1].set_text(f"{rpm} RPM")

    # ── Show / position ────────────────────────────────────────────────

    def show_near_top_right(self):
        """Toggle: drop down under the icon, or hide if already visible."""
        if self.get_visible():
            self.hide()
            return
        self._shown_at = time.monotonic()
        self.show_all()
        self.present()
        self._position_under_cursor()
        self.grab_focus()

    def _position_under_cursor(self):
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        _, cursor_x, _cursor_y = seat.get_pointer().get_position()
        w, _h = self.get_size()
        screen_w = Gdk.Screen.get_default().get_width()
        x = max(4, min(cursor_x - w // 2, screen_w - w - 4))
        self.move(x, 32)
