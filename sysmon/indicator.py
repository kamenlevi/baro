"""AppIndicator tray icon.

On GNOME a left-click can only open the system's native menu (the app
can't pop its own window from that click). So the stats live IN the menu:
clicking the icon opens it directly — no intermediate button — with a
donut gauge + exact details per component, the top processes, and entries
to open the big detailed donut panel and the settings.
"""
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")
from gi.repository import Gtk, GLib, Notify

from .icon_gen import generate_tray_icon, gen_donut_icon
from .monitor import SystemStats
from .popup import PopupWindow
from .settings import open_settings_dialog

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
    _HAS_INDICATOR = True
except Exception:
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator
        _HAS_INDICATOR = True
    except Exception:
        _HAS_INDICATOR = False


_last_warn_notify_time = [0.0]
_NOTIFY_COOLDOWN = 30.0
_GAUGE_PX = 24


class SysMonIndicator:
    def __init__(self, monitor, history, settings):
        self.monitor = monitor
        self.history = history
        self.settings = settings
        self._main_window = None
        self._last_stats = SystemStats()

        # Fan controller
        from .fans import detect_fans, FanCurveController
        self._fans = detect_fans()
        self._fan_controllable_by_label = {
            f.label: f.controllable for f in self._fans.values()
        }
        self._fan_controller = FanCurveController(
            self._fans, lambda: self._last_stats.cpu_temp
        )
        self._fan_controller.start()

        # Big detailed donut panel (opened from a menu entry).
        self._popup = PopupWindow(
            on_open_app=self._show_main_window,
            settings=settings,
            on_settings=lambda: self._on_settings(),
            on_quit=Gtk.main_quit,
        )
        self._popup._fan_controller = self._fan_controller

        # Live per-core utilization window.
        from .cores_window import CoresWindow
        self._cores = CoresWindow()

        if _HAS_INDICATOR:
            self._indicator = AppIndicator.Indicator.new(
                "sysmon",
                "utilities-system-monitor",
                AppIndicator.IndicatorCategory.HARDWARE,
            )
            self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self._indicator.set_menu(self._build_menu())
        else:
            self._status_icon = Gtk.StatusIcon()
            self._status_icon.set_from_icon_name("utilities-system-monitor")
            self._status_icon.connect("activate", lambda *_: self._open_panel())

        monitor.add_callback(self._on_stats)
        self._set_static_icon()
        self._refresh_menu(self._last_stats, [])
        self._update_label()
        GLib.timeout_add(1500, self._update_label)

    # ── Native stats menu ────────────────────────────────────────────────────

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        def gauge_row():
            it = Gtk.ImageMenuItem(label="")
            it.set_image(Gtk.Image())
            it.set_always_show_image(True)
            menu.append(it)
            sub = Gtk.Menu()
            it.set_submenu(sub)
            return it, sub

        # Each component: a donut row that opens a submenu of exact details.
        self._mi_cpu, self._sub_cpu = gauge_row()
        self._cpu_detail = self._make_detail_items(self._sub_cpu, 5)

        self._mi_gpu, self._sub_gpu = gauge_row()
        self._mi_gpu.set_no_show_all(True)
        self._gpu_detail = self._make_detail_items(self._sub_gpu, 5)

        self._mi_ram, self._sub_ram = gauge_row()
        self._ram_detail = self._make_detail_items(self._sub_ram, 4)

        menu.append(Gtk.SeparatorMenuItem())

        self._mi_proc_header = Gtk.MenuItem(label="Top processes")
        self._mi_proc_header.set_sensitive(False)
        menu.append(self._mi_proc_header)
        self._mi_procs = []
        for _ in range(5):
            it = Gtk.MenuItem(label="")
            it.set_no_show_all(True)
            menu.append(it)
            self._mi_procs.append(it)

        menu.append(Gtk.SeparatorMenuItem())

        panel_item = Gtk.MenuItem(label="Detailed panel…")
        panel_item.connect("activate", lambda *_: self._open_panel())
        menu.append(panel_item)

        cores_item = Gtk.MenuItem(label="CPU / GPU cores…")
        cores_item.connect("activate", lambda *_: self._open_cores())
        menu.append(cores_item)

        settings_item = Gtk.MenuItem(label="Settings…")
        settings_item.connect("activate", self._on_settings)
        menu.append(settings_item)

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _make_detail_items(self, submenu, n):
        items = []
        for _ in range(n):
            it = Gtk.MenuItem(label="")
            it.set_sensitive(False)
            it.set_no_show_all(True)
            submenu.append(it)
            items.append(it)
        submenu.show_all()
        return items

    @staticmethod
    def _fill_details(items, lines):
        for i, it in enumerate(items):
            if i < len(lines):
                it.set_visible(True)
                it.set_label(lines[i])
            else:
                it.set_visible(False)

    def _set_gauge(self, item, key, pct, label):
        item.set_label(label)
        img = item.get_image()
        if img is not None:
            img.set_from_file(gen_donut_icon(pct, key, size=_GAUGE_PX))

    def _refresh_menu(self, s: SystemStats, procs):
        if not _HAS_INDICATOR:
            return
        cfg = self.settings

        # CPU
        self._set_gauge(self._mi_cpu, "cpu", s.cpu_percent,
                        f"CPU   {s.cpu_percent:.0f}%")
        cpu_lines = [f"Usage:  {s.cpu_percent:.0f}%"]
        if s.cpu_freq_mhz > 0:
            cpu_lines.append(
                f"Frequency:  {s.cpu_freq_mhz/1000:.1f} / "
                f"{s.cpu_freq_max_mhz/1000:.1f} GHz")
        if cfg.show_temp and s.cpu_temp > 0:
            cpu_lines.append(f"Temperature:  {s.cpu_temp:.0f}°C")
        if s.cpu_per_core:
            cpu_lines.append("Cores:  " +
                             " ".join(f"{c:.0f}" for c in s.cpu_per_core[:8]))
        if s.thermal_throttling:
            cpu_lines.append("⚠ thermal throttling")
        self._fill_details(self._cpu_detail, cpu_lines)

        # GPU
        if cfg.show_gpu and s.gpu_available:
            self._mi_gpu.set_visible(True)
            self._set_gauge(self._mi_gpu, "gpu", s.gpu_percent,
                            f"GPU   {s.gpu_percent:.0f}%")
            gpu_lines = [f"Usage:  {s.gpu_percent:.0f}%"]
            if s.gpu_name:
                gpu_lines.append(s.gpu_name)
            if s.gpu_mem_total_mb > 0:
                gpu_lines.append(
                    f"VRAM:  {s.gpu_mem_used_mb/1024:.1f} / "
                    f"{s.gpu_mem_total_mb/1024:.1f} GB")
            if cfg.show_temp and s.gpu_temp > 0:
                gpu_lines.append(f"Temperature:  {s.gpu_temp:.0f}°C")
            if s.gpu_power_w > 0:
                gpu_lines.append(f"Power:  {s.gpu_power_w:.0f} W")
            self._fill_details(self._gpu_detail, gpu_lines)
        else:
            self._mi_gpu.set_visible(False)

        # Memory
        self._set_gauge(self._mi_ram, "ram", s.ram_percent,
                        f"Memory   {s.ram_percent:.0f}%")
        ram_lines = [
            f"Used:  {s.ram_used_gb:.1f} / {s.ram_total_gb:.1f} GB",
            f"Free:  {max(0.0, s.ram_total_gb - s.ram_used_gb):.1f} GB",
        ]
        if s.swap_total_gb > 0:
            ram_lines.append(
                f"Swap:  {s.swap_used_gb:.1f} / {s.swap_total_gb:.1f} GB")
        self._fill_details(self._ram_detail, ram_lines)

        # Top processes
        procs = procs or []
        self._mi_proc_header.set_visible(bool(procs))
        for i, it in enumerate(self._mi_procs):
            if i < len(procs):
                p = procs[i]
                it.set_visible(True)
                it.set_label(f"    {p.name}    {p.cpu_percent:.0f}%")
            else:
                it.set_visible(False)

    # ── Big detailed panel ───────────────────────────────────────────────────

    def _open_panel(self):
        if not self._popup.get_visible():
            try:
                from .processes import collect_top_processes
                procs = collect_top_processes(5, sort_by="cpu")
            except Exception:
                procs = []
            self._popup.update(self._last_stats, procs)
        self._popup.show_near_top_right()

    def _open_cores(self):
        self._cores.update(self._last_stats)
        self._cores.present_window()

    # ── Stats callback ───────────────────────────────────────────────────────

    def _on_stats(self, s: SystemStats):
        self._last_stats = s
        if s.fans:
            ctrl_by_label = self._fan_controllable_by_label
            s.fans = [
                (label, rpm, ctrl_by_label.get(label, False))
                for label, rpm, _ in s.fans
            ]
        try:
            from .processes import collect_top_processes
            procs = collect_top_processes(5, sort_by="cpu")
        except Exception:
            procs = []
        GLib.idle_add(self._refresh_menu, s, procs)
        if self._popup.get_visible():
            GLib.idle_add(self._popup.update, s, procs)
        if self._cores.get_visible():
            GLib.idle_add(self._cores.update, s)
        self._maybe_notify(s)

    def _maybe_notify(self, s: SystemStats):
        if not s.warnings:
            return
        if not self.settings.notify_desktop:
            return
        now = time.time()
        if now - _last_warn_notify_time[0] < _NOTIFY_COOLDOWN:
            return
        _last_warn_notify_time[0] = now
        body = "\n".join(f"• {w}" for w in s.warnings)
        n = Notify.Notification.new("⚠ SysMon Warning", body, "dialog-warning")
        n.set_urgency(Notify.Urgency.CRITICAL)
        try:
            n.show()
        except Exception:
            pass

    # ── Menu-bar icon + label ────────────────────────────────────────────────

    def _set_static_icon(self):
        """Set the menu-bar icon once. It never changes, so it never blinks."""
        icon_path = generate_tray_icon()
        if _HAS_INDICATOR:
            import os
            icon_dir = os.path.dirname(icon_path)
            icon_name = os.path.splitext(os.path.basename(icon_path))[0]
            self._indicator.set_icon_theme_path(icon_dir)
            self._indicator.set_icon_full(icon_name, "system monitor")
        else:
            try:
                from gi.repository import GdkPixbuf
                pb = GdkPixbuf.Pixbuf.new_from_file(icon_path)
                self._status_icon.set_from_pixbuf(pb)
            except Exception:
                pass

    def _update_label(self) -> bool:
        """Show live CPU/RAM as fixed-width text — updates without flicker."""
        if not _HAS_INDICATOR:
            return True
        s = self._last_stats
        parts = [f"CPU {s.cpu_percent:3.0f}%"]
        if self.settings.show_gpu and s.gpu_available:
            parts.append(f"GPU {s.gpu_percent:3.0f}%")
        parts.append(f"RAM {s.ram_percent:3.0f}%")
        label = "  ".join(parts)
        guide = "  ".join("CPU 100%" for _ in parts)
        self._indicator.set_label(label, guide)
        return True

    # ── Other windows ────────────────────────────────────────────────────────

    def _show_main_window(self):
        if self._main_window is None:
            app = _DummyApp()
            from .main_window import MainWindow
            self._main_window = MainWindow(
                app, self.monitor, self.history, self.settings,
                fan_channels=self._fans,
                fan_controller=self._fan_controller,
            )
        self._main_window.present()

    def _on_settings(self, *_):
        open_settings_dialog(self.settings, parent=self._main_window)


class _DummyApp(Gtk.Application):
    """Minimal Gtk.Application shim so MainWindow can call super().__init__(application=app)."""
    def __init__(self):
        super().__init__(application_id="com.sysmon.app")
        self.register()
