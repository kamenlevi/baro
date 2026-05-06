"""AppIndicator3 tray icon with live icon generation and popup panel."""
import time
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")
from gi.repository import Gtk, GLib, Notify

from .icon_gen import generate_tray_icon
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
        self._fan_controller = FanCurveController(
            self._fans, lambda: self._last_stats.cpu_temp
        )
        self._fan_controller.start()

        self._popup = PopupWindow(
            on_open_app=self._show_main_window,
            settings=settings,
        )
        self._popup._fan_controller = self._fan_controller

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
            self._status_icon.connect("activate", self._on_tray_click)

        monitor.add_callback(self._on_stats)
        GLib.timeout_add(1500, self._update_icon)

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        item_popup = Gtk.MenuItem(label="Quick Stats…")
        item_popup.connect("activate", lambda *_: self._toggle_popup())
        menu.append(item_popup)

        item_open = Gtk.MenuItem(label="Open Full Monitor")
        item_open.connect("activate", lambda *_: self._show_main_window())
        menu.append(item_open)

        menu.append(Gtk.SeparatorMenuItem())

        item_settings = Gtk.MenuItem(label="Settings…")
        item_settings.connect("activate", self._on_settings)
        menu.append(item_settings)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit SysMon")
        item_quit.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        return menu

    def _on_stats(self, s: SystemStats):
        self._last_stats = s
        # Enrich fan data with controllable flag from detected fans
        enriched = [
            (label, rpm, any(
                f.controllable for f in self._fans.values() if f.label == label
            ))
            for label, rpm, _ in s.fans
        ]
        s.fans = enriched if enriched else s.fans
        GLib.idle_add(self._popup.update, s)
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

    def _update_icon(self) -> bool:
        s = self._last_stats
        has_warn = bool(s.warnings)

        icon_path = generate_tray_icon(
            cpu_pct=s.cpu_percent,
            ram_pct=s.ram_percent,
            gpu_pct=s.gpu_percent if s.gpu_available else 0.0,
            has_gpu=s.gpu_available and self.settings.show_gpu,
            has_warning=has_warn,
        )

        if _HAS_INDICATOR:
            import os
            icon_dir = os.path.dirname(icon_path)
            icon_name = os.path.splitext(os.path.basename(icon_path))[0]
            self._indicator.set_icon_theme_path(icon_dir)
            self._indicator.set_icon_full(icon_name, "system monitor")

            if self.settings.show_label:
                parts = []
                if self.settings.show_cpu:
                    parts.append(f"CPU {s.cpu_percent:.0f}%")
                if self.settings.show_gpu and s.gpu_available:
                    parts.append(f"GPU {s.gpu_percent:.0f}%")
                if self.settings.show_ram:
                    parts.append(f"RAM {s.ram_percent:.0f}%")
                label = "  ".join(parts)
                if has_warn:
                    label = "⚠ " + label
                self._indicator.set_label(label, "CPU 100%  GPU 100%  RAM 100%")
            else:
                self._indicator.set_label("", "")
        else:
            try:
                from gi.repository import GdkPixbuf
                pb = GdkPixbuf.Pixbuf.new_from_file(icon_path)
                self._status_icon.set_from_pixbuf(pb)
            except Exception:
                pass

        return True  # keep timer

    def _toggle_popup(self):
        if self._popup.get_visible():
            self._popup.hide()
        else:
            self._popup.show_near_top_right()

    def _on_tray_click(self, *_):
        self._toggle_popup()

    def _show_main_window(self):
        if self._main_window is None:
            app = _DummyApp()
            from .main_window import MainWindow
            self._main_window = MainWindow(app, self.monitor, self.history, self.settings)
        self._main_window.present()
        self._popup.hide()

    def _on_settings(self, *_):
        open_settings_dialog(self.settings, parent=self._main_window)


class _DummyApp(Gtk.Application):
    """Minimal Gtk.Application shim so MainWindow can call super().__init__(application=app)."""
    def __init__(self):
        super().__init__(application_id="com.sysmon.app")
        self.register()
