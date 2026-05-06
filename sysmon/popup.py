"""Popup panel window shown when tray icon is clicked."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

from .monitor import SystemStats


CSS = b"""
window.sysmon-popup {
    background-color: #1e1e2e;
    border-radius: 10px;
    border: 1px solid #444466;
}
label.section-title {
    color: #cdd6f4;
    font-weight: bold;
    font-size: 10px;
    letter-spacing: 1px;
}
label.value {
    color: #89dceb;
    font-size: 12px;
    font-family: monospace;
}
label.warn {
    color: #f38ba8;
}
label.ok {
    color: #a6e3a1;
}
label.warning-text {
    color: #fab387;
    font-size: 10px;
}
button.open-btn {
    background-color: #313244;
    color: #cdd6f4;
    border-radius: 6px;
    border: none;
    padding: 4px 10px;
}
button.open-btn:hover {
    background-color: #45475a;
}
progressbar trough {
    background-color: #313244;
    border-radius: 3px;
    min-height: 6px;
}
progressbar progress {
    background-color: #89b4fa;
    border-radius: 3px;
    min-height: 6px;
}
progressbar.warn progress {
    background-color: #fab387;
}
progressbar.crit progress {
    background-color: #f38ba8;
}
"""


def _apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


_css_applied = False


def _pbar(pct: float) -> Gtk.ProgressBar:
    pb = Gtk.ProgressBar()
    pb.set_fraction(min(pct / 100.0, 1.0))
    pb.set_size_request(160, -1)
    ctx = pb.get_style_context()
    ctx.add_class("progressbar")
    if pct >= 90:
        ctx.add_class("crit")
    elif pct >= 70:
        ctx.add_class("warn")
    return pb


def _lbl(text: str, css_class: str = "value") -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0.0)
    lbl.get_style_context().add_class(css_class)
    return lbl


def _section_title(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text.upper(), xalign=0.0)
    lbl.get_style_context().add_class("section-title")
    lbl.set_margin_top(8)
    return lbl


class PopupWindow(Gtk.Window):
    def __init__(self, on_open_app, settings):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.on_open_app = on_open_app
        self.settings = settings

        global _css_applied
        if not _css_applied:
            _apply_css()
            _css_applied = True

        self.get_style_context().add_class("sysmon-popup")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_resizable(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_start(14)
        outer.set_margin_end(14)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)
        self.add(outer)

        # Title row
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_lbl = Gtk.Label(xalign=0)
        title_lbl.set_markup('<span font="11" weight="bold" color="#cdd6f4">System Monitor</span>')
        title_row.pack_start(title_lbl, True, True, 0)
        self._warn_icon = Gtk.Label(label="⚠", xalign=1.0)
        self._warn_icon.get_style_context().add_class("warn")
        self._warn_icon.set_no_show_all(True)
        title_row.pack_start(self._warn_icon, False, False, 0)
        outer.pack_start(title_row, False, False, 0)

        sep = Gtk.Separator()
        sep.set_margin_top(6)
        sep.set_margin_bottom(4)
        outer.pack_start(sep, False, False, 0)

        # Content grid
        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(3)
        outer.pack_start(grid, False, False, 0)

        row = [0]

        def add_row(*widgets):
            for col, w in enumerate(widgets):
                grid.attach(w, col, row[0], 1, 1)
            row[0] += 1

        def add_section(title):
            lbl = _section_title(title)
            lbl.set_margin_top(8)
            grid.attach(lbl, 0, row[0], 3, 1)
            row[0] += 1

        # CPU
        add_section("CPU")
        self._cpu_pct_lbl = _lbl("--.-% ")
        self._cpu_pbar = _pbar(0)
        self._cpu_temp_lbl = _lbl("--°C")
        add_row(self._cpu_pct_lbl, self._cpu_pbar, self._cpu_temp_lbl)

        self._cpu_freq_lbl = _lbl("")
        self._cpu_freq_lbl.get_style_context().add_class("section-title")
        grid.attach(self._cpu_freq_lbl, 0, row[0], 3, 1)
        row[0] += 1

        # GPU (hidden if no GPU)
        self._gpu_section = _section_title("GPU")
        grid.attach(self._gpu_section, 0, row[0], 3, 1)
        row[0] += 1

        self._gpu_pct_lbl = _lbl("--.-% ")
        self._gpu_pbar = _pbar(0)
        self._gpu_temp_lbl = _lbl("--°C")
        add_row(self._gpu_pct_lbl, self._gpu_pbar, self._gpu_temp_lbl)

        self._gpu_vram_lbl = _lbl("")
        self._gpu_vram_lbl.get_style_context().add_class("section-title")
        grid.attach(self._gpu_vram_lbl, 0, row[0], 3, 1)
        row[0] += 1

        self._gpu_rows = [
            self._gpu_section, self._gpu_pct_lbl,
            self._gpu_pbar, self._gpu_temp_lbl, self._gpu_vram_lbl,
        ]

        # RAM
        add_section("RAM")
        self._ram_pct_lbl = _lbl("--.-% ")
        self._ram_pbar = _pbar(0)
        self._ram_detail_lbl = _lbl("")
        add_row(self._ram_pct_lbl, self._ram_pbar, self._ram_detail_lbl)

        self._swap_lbl = _lbl("")
        self._swap_lbl.get_style_context().add_class("section-title")
        grid.attach(self._swap_lbl, 0, row[0], 3, 1)
        row[0] += 1

        # Fan section (built dynamically when fan data arrives)
        self._fan_section_title = _section_title("Fans")
        self._fan_section_title.set_no_show_all(True)
        outer.pack_start(self._fan_section_title, False, False, 0)

        self._fan_rpm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._fan_rpm_box.set_no_show_all(True)
        outer.pack_start(self._fan_rpm_box, False, False, 0)

        # Fan curve editor (compact, shown inline)
        self._curve_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._curve_toggle = Gtk.ToggleButton(label="Edit Fan Curve ▾")
        self._curve_toggle.get_style_context().add_class("open-btn")
        self._curve_toggle.connect("toggled", self._on_curve_toggle)
        self._curve_header_box.pack_start(self._curve_toggle, False, False, 0)
        self._curve_header_box.set_no_show_all(True)
        self._curve_header_box.set_margin_top(4)
        outer.pack_start(self._curve_header_box, False, False, 0)

        self._curve_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._curve_panel.set_no_show_all(True)
        self._curve_panel.set_margin_start(2)
        self._curve_panel.set_margin_end(2)
        outer.pack_start(self._curve_panel, False, False, 0)

        self._curve_editor = None          # created lazily
        self._fan_controller = None        # set by indicator after init
        self._active_fan_key = None        # which fan the curve applies to

        # Warning area
        self._warn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._warn_box.set_margin_top(6)
        self._warn_box.set_no_show_all(True)
        outer.pack_start(self._warn_box, False, False, 0)

        sep2 = Gtk.Separator()
        sep2.set_margin_top(8)
        sep2.set_margin_bottom(6)
        outer.pack_start(sep2, False, False, 0)

        # Bottom button row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_btn = Gtk.Button(label="Open Full Monitor")
        open_btn.get_style_context().add_class("open-btn")
        open_btn.connect("clicked", lambda *_: self.on_open_app())
        btn_row.pack_end(open_btn, False, False, 0)
        outer.pack_start(btn_row, False, False, 0)

        self.connect("focus-out-event", lambda *_: self.hide())

        self.show_all()
        self.hide()

    def _set_pbar(self, pb: Gtk.ProgressBar, pct: float):
        pb.set_fraction(min(pct / 100.0, 1.0))
        ctx = pb.get_style_context()
        ctx.remove_class("warn")
        ctx.remove_class("crit")
        if pct >= 90:
            ctx.add_class("crit")
        elif pct >= 70:
            ctx.add_class("warn")

    def update(self, s: SystemStats):
        cfg = self.settings

        if cfg.show_cpu:
            self._cpu_pct_lbl.set_text(f"{s.cpu_percent:5.1f}% ")
            self._set_pbar(self._cpu_pbar, s.cpu_percent)
            if cfg.show_temp and s.cpu_temp > 0:
                color = "#f38ba8" if s.cpu_temp > cfg.warn_cpu_temp else "#a6e3a1"
                self._cpu_temp_lbl.set_markup(
                    f'<span color="{color}">{s.cpu_temp:.0f}°C</span>'
                )
            else:
                self._cpu_temp_lbl.set_text("")
            if s.cpu_freq_mhz > 0:
                throttle = " ⚡throttled" if s.thermal_throttling else ""
                self._cpu_freq_lbl.set_text(
                    f"  {s.cpu_freq_mhz:.0f} / {s.cpu_freq_max_mhz:.0f} MHz{throttle}"
                )

        if cfg.show_gpu and s.gpu_available:
            for w in self._gpu_rows:
                w.set_visible(True)
            self._gpu_pct_lbl.set_text(f"{s.gpu_percent:5.1f}% ")
            self._set_pbar(self._gpu_pbar, s.gpu_percent)
            if cfg.show_temp and s.gpu_temp > 0:
                color = "#f38ba8" if s.gpu_temp > cfg.warn_gpu_temp else "#a6e3a1"
                self._gpu_temp_lbl.set_markup(
                    f'<span color="{color}">{s.gpu_temp:.0f}°C</span>'
                )
            if s.gpu_mem_total_mb > 0:
                self._gpu_vram_lbl.set_text(
                    f"  VRAM {s.gpu_mem_used_mb/1024:.1f} / {s.gpu_mem_total_mb/1024:.1f} GB"
                    + (f"  {s.gpu_power_w:.0f}W" if s.gpu_power_w > 0 else "")
                )
        else:
            for w in self._gpu_rows:
                w.set_visible(False)

        if cfg.show_ram:
            self._ram_pct_lbl.set_text(f"{s.ram_percent:5.1f}% ")
            self._set_pbar(self._ram_pbar, s.ram_percent)
            self._ram_detail_lbl.set_text(
                f"{s.ram_used_gb:.1f}/{s.ram_total_gb:.1f}GB"
            )
            if s.swap_total_gb > 0:
                self._swap_lbl.set_text(
                    f"  Swap {s.swap_used_gb:.1f}/{s.swap_total_gb:.1f}GB "
                    f"({s.swap_percent:.0f}%)"
                )

        # Warnings
        for child in self._warn_box.get_children():
            self._warn_box.remove(child)
        if s.warnings:
            self._warn_box.set_visible(True)
            self._warn_icon.set_visible(True)
            for w in s.warnings:
                lbl = Gtk.Label(label=f"⚠  {w}", xalign=0.0)
                lbl.get_style_context().add_class("warning-text")
                self._warn_box.pack_start(lbl, False, False, 0)
                lbl.show()
        else:
            self._warn_box.set_visible(False)
            self._warn_icon.set_visible(False)

        # Fans
        self._update_fans(s.fans)

    def _update_fans(self, fans):
        if not fans:
            self._fan_section_title.set_visible(False)
            self._fan_rpm_box.set_visible(False)
            self._curve_header_box.set_visible(False)
            return

        self._fan_section_title.set_visible(True)
        self._fan_rpm_box.set_visible(True)

        # Rebuild RPM rows only if fan count changed
        existing = self._fan_rpm_box.get_children()
        if len(existing) != len(fans):
            for c in existing:
                self._fan_rpm_box.remove(c)
            for label, rpm, controllable in fans:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                row.set_margin_start(4)
                name_lbl = _lbl(f"  {label}", "section-title")
                name_lbl.set_size_request(120, -1)
                rpm_lbl = _lbl(f"{rpm} RPM", "value")
                rpm_lbl.set_size_request(80, -1)
                pb = Gtk.ProgressBar()
                pb.set_size_request(80, -1)
                row.pack_start(name_lbl, False, False, 0)
                row.pack_start(rpm_lbl, False, False, 0)
                row.pack_start(pb, False, False, 0)
                row._rpm_lbl = rpm_lbl
                row._pb = pb
                self._fan_rpm_box.pack_start(row, False, False, 0)
                row.show_all()
        else:
            # Update values in place
            for row, (label, rpm, _) in zip(existing, fans):
                if hasattr(row, "_rpm_lbl"):
                    row._rpm_lbl.set_text(f"{rpm} RPM")
                    max_rpm = 4000
                    row._pb.set_fraction(min(rpm / max_rpm, 1.0))

        # Show curve editor toggle if any fan is controllable
        has_controllable = any(c for _, _, c in fans)
        # Always show if fan controller is attached (even if not yet writable)
        if self._fan_controller is not None:
            self._curve_header_box.set_visible(True)

    def _on_curve_toggle(self, btn):
        if btn.get_active():
            btn.set_label("Fan Curve ▴")
            self._build_curve_panel()
            self._curve_panel.set_visible(True)
            self._curve_panel.show_all()
        else:
            btn.set_label("Edit Fan Curve ▾")
            self._curve_panel.set_visible(False)
        # Resize popup to fit
        self.resize(1, 1)

    def _build_curve_panel(self):
        # Clear previous contents
        for c in self._curve_panel.get_children():
            self._curve_panel.remove(c)

        from .fan_curve_widget import FanCurveEditor
        from .fans import DEFAULT_CURVE

        # Fan selector (if multiple fans)
        if self._fan_controller:
            fan_keys = list(self._fan_controller._fans.keys())
            if len(fan_keys) > 1:
                sel_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                fan_sel = Gtk.ComboBoxText()
                for key in fan_keys:
                    fan = self._fan_controller._fans[key]
                    fan_sel.append(key, fan.label)
                fan_sel.set_active(0)
                if not self._active_fan_key:
                    self._active_fan_key = fan_keys[0]
                fan_sel.connect("changed", self._on_fan_select)
                sel_box.pack_start(Gtk.Label(label="Fan:", xalign=0), False, False, 0)
                sel_box.pack_start(fan_sel, True, True, 0)
                self._curve_panel.pack_start(sel_box, False, False, 0)
            elif fan_keys:
                self._active_fan_key = fan_keys[0]

            # Load existing curve for selected fan
            initial = DEFAULT_CURVE
            if self._active_fan_key:
                fan = self._fan_controller._fans.get(self._active_fan_key)
                if fan:
                    initial = fan.curve

            editor = FanCurveEditor(points=list(initial), compact=True)
            editor.connect("curve-changed", self._on_curve_changed)
            self._curve_editor = editor
            self._curve_panel.pack_start(editor, False, False, 0)

            # Enable toggle + apply row
            ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            ctrl_row.set_margin_top(4)

            enable_sw = Gtk.Switch()
            if self._active_fan_key:
                enable_sw.set_active(
                    self._fan_controller.is_active(self._active_fan_key)
                )
            enable_sw.connect("notify::active", self._on_enable_toggle)
            self._enable_switch = enable_sw

            sw_lbl = Gtk.Label(label="Apply curve", xalign=0.0)
            sw_lbl.get_style_context().add_class("section-title")

            reset_btn = Gtk.Button(label="Reset to Auto")
            reset_btn.get_style_context().add_class("open-btn")
            reset_btn.connect("clicked", self._on_reset_auto)

            ctrl_row.pack_start(sw_lbl, False, False, 0)
            ctrl_row.pack_start(enable_sw, False, False, 4)
            ctrl_row.pack_end(reset_btn, False, False, 0)
            self._curve_panel.pack_start(ctrl_row, False, False, 0)

            # Permission warning if not writable
            from .fans import check_pwm_writable
            if not check_pwm_writable():
                warn_lbl = Gtk.Label(
                    label="⚠ Fan control needs root or udev rule.\nSee README for setup.",
                    xalign=0.0,
                )
                warn_lbl.get_style_context().add_class("warning-text")
                warn_lbl.set_line_wrap(True)
                self._curve_panel.pack_start(warn_lbl, False, False, 0)
        else:
            # No controller yet
            lbl = Gtk.Label(label="Fan controller not initialized.", xalign=0.0)
            lbl.get_style_context().add_class("warning-text")
            self._curve_panel.pack_start(lbl, False, False, 0)

    def _on_fan_select(self, combo):
        self._active_fan_key = combo.get_active_id()
        if self._curve_editor and self._fan_controller and self._active_fan_key:
            fan = self._fan_controller._fans.get(self._active_fan_key)
            if fan:
                self._curve_editor.set_points(list(fan.curve))

    def _on_curve_changed(self, editor, points):
        if self._fan_controller and self._active_fan_key:
            self._fan_controller.update_curve(self._active_fan_key, points)

    def _on_enable_toggle(self, sw, _param):
        if self._fan_controller and self._active_fan_key:
            self._fan_controller.set_curve_active(self._active_fan_key, sw.get_active())

    def _on_reset_auto(self, _):
        if self._fan_controller and self._active_fan_key:
            self._fan_controller.set_curve_active(self._active_fan_key, False)
            if hasattr(self, "_enable_switch"):
                self._enable_switch.set_active(False)

    def show_near_top_right(self):
        screen = Gdk.Screen.get_default()
        sw = screen.get_width()
        self.show_all()
        self.present()
        w, h = self.get_size()
        margin = 8
        self.move(sw - w - margin, 32 + margin)
        self.grab_focus()
