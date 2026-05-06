"""Full-screen monitor application window with tabs and graphs."""
import time
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

from .graphs import RollingGraph, COLORS
from .monitor import SystemStats
from .settings import open_settings_dialog
from .process_tab import ProcessTab

CSS_MAIN = b"""
window.main-win {
    background-color: #1e1e2e;
}
notebook tab {
    background-color: #181825;
    color: #7f849c;
    padding: 4px 10px;
    border: none;
}
notebook tab:checked {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border-bottom: 2px solid #89b4fa;
}
label.h1 {
    color: #cdd6f4;
    font-size: 14px;
    font-weight: bold;
}
label.metric {
    color: #89dceb;
    font-family: monospace;
    font-size: 12px;
}
label.metric-warn {
    color: #f38ba8;
    font-family: monospace;
    font-size: 12px;
}
label.unit {
    color: #6c7086;
    font-size: 11px;
}
frame {
    border: 1px solid #313244;
    border-radius: 6px;
}
frame > label {
    color: #89b4fa;
    font-size: 10px;
    font-weight: bold;
    padding: 0 4px;
}
progressbar trough {
    background-color: #313244;
    border-radius: 4px;
    min-height: 10px;
}
progressbar progress {
    background-color: #89b4fa;
    border-radius: 4px;
}
progressbar.warn progress { background-color: #fab387; }
progressbar.crit progress { background-color: #f38ba8; }
progressbar.green progress { background-color: #a6e3a1; }
"""

_CSS_APPLIED = [False]


def _apply_css():
    if _CSS_APPLIED[0]:
        return
    p = Gtk.CssProvider()
    p.load_from_data(CSS_MAIN)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _CSS_APPLIED[0] = True


def _metric_label(initial="--") -> Gtk.Label:
    lbl = Gtk.Label(label=initial, xalign=0.0)
    lbl.get_style_context().add_class("metric")
    return lbl


def _unit_label(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0.0)
    lbl.get_style_context().add_class("unit")
    return lbl


def _pbar(pct=0.0, style="") -> Gtk.ProgressBar:
    pb = Gtk.ProgressBar()
    pb.set_fraction(min(pct / 100.0, 1.0))
    if style:
        pb.get_style_context().add_class(style)
    return pb


def _update_pbar(pb: Gtk.ProgressBar, pct: float):
    pb.set_fraction(min(pct / 100.0, 1.0))
    ctx = pb.get_style_context()
    for c in ("warn", "crit", "green"):
        ctx.remove_class(c)
    if pct >= 90:
        ctx.add_class("crit")
    elif pct >= 70:
        ctx.add_class("warn")
    else:
        ctx.add_class("green")


def _framed(title: str, child: Gtk.Widget) -> Gtk.Frame:
    f = Gtk.Frame(label=f" {title} ")
    f.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
    f.set_margin_start(6)
    f.set_margin_end(6)
    f.set_margin_top(4)
    f.set_margin_bottom(4)
    child.set_margin_start(8)
    child.set_margin_end(8)
    child.set_margin_top(6)
    child.set_margin_bottom(8)
    f.add(child)
    return f


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, monitor, history, settings):
        super().__init__(application=app, title="SysMon – System Monitor")
        self.monitor = monitor
        self.history = history
        self.settings = settings

        _apply_css()
        self.get_style_context().add_class("main-win")
        self.set_default_size(820, 620)
        self.set_position(Gtk.WindowPosition.CENTER)

        header = Gtk.HeaderBar(title="System Monitor", show_close_button=True)
        header.set_subtitle("Real-time performance")
        self.set_titlebar(header)

        settings_btn = Gtk.Button()
        settings_btn.set_image(Gtk.Image.new_from_icon_name("preferences-system", Gtk.IconSize.BUTTON))
        settings_btn.connect("clicked", self._open_settings)
        header.pack_end(settings_btn)

        notebook = Gtk.Notebook()
        notebook.set_tab_pos(Gtk.PositionType.TOP)
        self.add(notebook)

        self._graphs = {}
        self._last_history_record = 0.0

        notebook.append_page(self._build_overview_tab(), Gtk.Label(label="Overview"))
        notebook.append_page(self._build_cpu_tab(), Gtk.Label(label="CPU"))
        notebook.append_page(self._build_gpu_tab(), Gtk.Label(label="GPU"))
        notebook.append_page(self._build_ram_tab(), Gtk.Label(label="Memory"))
        notebook.append_page(self._build_history_tab(), Gtk.Label(label="History"))
        self._proc_tab = ProcessTab()
        notebook.append_page(self._proc_tab, Gtk.Label(label="Processes"))

        monitor.add_callback(self._on_stats)
        GLib.timeout_add(2000, self._redraw_graphs)

        self.connect("delete-event", lambda *_: self.hide() or True)
        self.show_all()

    # ── Overview tab ────────────────────────────────────────────────────────

    def _build_overview_tab(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        scroll.add(box)

        # CPU summary
        cpu_box = Gtk.Grid()
        cpu_box.set_column_spacing(12)
        cpu_box.set_row_spacing(4)

        self._ov_cpu_pct = _metric_label()
        self._ov_cpu_temp = _metric_label()
        self._ov_cpu_freq = _metric_label()
        self._ov_cpu_pb = _pbar()
        self._ov_throttle = Gtk.Label(label="", xalign=0.0)
        self._ov_throttle.get_style_context().add_class("metric-warn")

        cpu_box.attach(Gtk.Label(label="Usage", xalign=0), 0, 0, 1, 1)
        cpu_box.attach(self._ov_cpu_pct, 1, 0, 1, 1)
        cpu_box.attach(self._ov_cpu_pb, 2, 0, 1, 1)
        cpu_box.attach(Gtk.Label(label="Temp", xalign=0), 0, 1, 1, 1)
        cpu_box.attach(self._ov_cpu_temp, 1, 1, 1, 1)
        cpu_box.attach(Gtk.Label(label="Freq", xalign=0), 0, 2, 1, 1)
        cpu_box.attach(self._ov_cpu_freq, 1, 2, 2, 1)
        cpu_box.attach(self._ov_throttle, 1, 3, 2, 1)
        box.pack_start(_framed("CPU", cpu_box), False, False, 0)

        # GPU summary
        gpu_box = Gtk.Grid()
        gpu_box.set_column_spacing(12)
        gpu_box.set_row_spacing(4)
        self._ov_gpu_pct = _metric_label()
        self._ov_gpu_temp = _metric_label()
        self._ov_gpu_vram = _metric_label()
        self._ov_gpu_pb = _pbar()
        self._ov_gpu_name = Gtk.Label(label="", xalign=0.0)
        self._ov_gpu_name.get_style_context().add_class("unit")
        self._ov_gpu_power = _metric_label()

        gpu_box.attach(self._ov_gpu_name, 0, 0, 3, 1)
        gpu_box.attach(Gtk.Label(label="Usage", xalign=0), 0, 1, 1, 1)
        gpu_box.attach(self._ov_gpu_pct, 1, 1, 1, 1)
        gpu_box.attach(self._ov_gpu_pb, 2, 1, 1, 1)
        gpu_box.attach(Gtk.Label(label="Temp", xalign=0), 0, 2, 1, 1)
        gpu_box.attach(self._ov_gpu_temp, 1, 2, 1, 1)
        gpu_box.attach(Gtk.Label(label="VRAM", xalign=0), 0, 3, 1, 1)
        gpu_box.attach(self._ov_gpu_vram, 1, 3, 2, 1)
        gpu_box.attach(Gtk.Label(label="Power", xalign=0), 0, 4, 1, 1)
        gpu_box.attach(self._ov_gpu_power, 1, 4, 1, 1)
        self._ov_gpu_frame = _framed("GPU", gpu_box)
        box.pack_start(self._ov_gpu_frame, False, False, 0)

        # RAM
        ram_box = Gtk.Grid()
        ram_box.set_column_spacing(12)
        ram_box.set_row_spacing(4)
        self._ov_ram_pct = _metric_label()
        self._ov_ram_detail = _metric_label()
        self._ov_ram_pb = _pbar()
        self._ov_swap = _metric_label()

        ram_box.attach(Gtk.Label(label="Usage", xalign=0), 0, 0, 1, 1)
        ram_box.attach(self._ov_ram_pct, 1, 0, 1, 1)
        ram_box.attach(self._ov_ram_pb, 2, 0, 1, 1)
        ram_box.attach(Gtk.Label(label="Used", xalign=0), 0, 1, 1, 1)
        ram_box.attach(self._ov_ram_detail, 1, 1, 2, 1)
        ram_box.attach(Gtk.Label(label="Swap", xalign=0), 0, 2, 1, 1)
        ram_box.attach(self._ov_swap, 1, 2, 2, 1)
        box.pack_start(_framed("Memory", ram_box), False, False, 0)

        # Warnings box
        self._ov_warn_frame = _framed("⚠  Warnings", Gtk.Box())
        self._ov_warn_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._ov_warn_inner.set_margin_start(8)
        self._ov_warn_inner.set_margin_end(8)
        self._ov_warn_inner.set_margin_top(4)
        self._ov_warn_inner.set_margin_bottom(6)
        # rebuild framed with inner box
        for child in self._ov_warn_frame.get_children():
            self._ov_warn_frame.remove(child)
        self._ov_warn_frame.add(self._ov_warn_inner)
        self._ov_warn_frame.set_no_show_all(True)
        box.pack_start(self._ov_warn_frame, False, False, 0)

        # Top Processes summary
        self._ov_top_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._ov_top_inner.set_margin_start(8)
        self._ov_top_inner.set_margin_end(8)
        self._ov_top_inner.set_margin_top(4)
        self._ov_top_inner.set_margin_bottom(6)
        box.pack_start(_framed("Top Processes", self._ov_top_inner), False, False, 0)
        GLib.timeout_add(4000, self._refresh_overview_procs)
        GLib.idle_add(self._refresh_overview_procs)

        return scroll

    # ── CPU tab ─────────────────────────────────────────────────────────────

    def _build_cpu_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)

        g_usage = RollingGraph(
            "CPU Usage", [("cpu", "Total %", COLORS["cpu"])],
            y_label="%", window_sec=self.settings.graph_window_sec
        )
        g_temp = RollingGraph(
            "CPU Temperature", [("cpu_temp", "°C", COLORS["cpu_temp"])],
            y_label="°C", y_max=110, window_sec=self.settings.graph_window_sec
        )
        self._graphs["cpu_usage"] = g_usage
        self._graphs["cpu_temp"] = g_temp
        box.pack_start(g_usage, True, True, 0)
        box.pack_start(g_temp, True, True, 0)

        # Per-core bars
        cores_frame_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cores_frame_inner.set_margin_start(8)
        cores_frame_inner.set_margin_end(8)
        cores_frame_inner.set_margin_top(4)
        cores_frame_inner.set_margin_bottom(6)
        self._core_bars = []
        self._core_labels = []
        n_cores = len(self.monitor.get_stats().cpu_per_core) or 4
        for i in range(n_cores):
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            pb = _pbar()
            pb.set_orientation(Gtk.Orientation.VERTICAL)
            pb.set_inverted(True)
            pb.set_size_request(18, 60)
            lbl = Gtk.Label(label=f"C{i}", xalign=0.5)
            lbl.get_style_context().add_class("unit")
            vb.pack_start(pb, True, True, 0)
            vb.pack_start(lbl, False, False, 0)
            cores_frame_inner.pack_start(vb, False, False, 0)
            self._core_bars.append(pb)
            self._core_labels.append(lbl)
        box.pack_start(_framed("Per-core Usage", cores_frame_inner), False, False, 0)
        return box

    # ── GPU tab ─────────────────────────────────────────────────────────────

    def _build_gpu_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)

        self._gpu_tab_label = Gtk.Label(label="No GPU detected", xalign=0.0)
        self._gpu_tab_label.get_style_context().add_class("unit")
        self._gpu_tab_label.set_margin_start(8)
        box.pack_start(self._gpu_tab_label, False, False, 0)

        g_usage = RollingGraph(
            "GPU Usage",
            [("gpu", "Usage %", COLORS["gpu"]),
             ("gpu_mem", "VRAM %", "#cba6f7")],
            y_label="%", window_sec=self.settings.graph_window_sec
        )
        g_temp = RollingGraph(
            "GPU Temperature", [("gpu_temp", "°C", COLORS["gpu_temp"])],
            y_label="°C", y_max=110, window_sec=self.settings.graph_window_sec
        )
        self._graphs["gpu_usage"] = g_usage
        self._graphs["gpu_temp"] = g_temp
        box.pack_start(g_usage, True, True, 0)
        box.pack_start(g_temp, True, True, 0)
        return box

    # ── RAM tab ─────────────────────────────────────────────────────────────

    def _build_ram_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)

        g_ram = RollingGraph(
            "Memory Usage",
            [("ram", "RAM %", COLORS["ram"]),
             ("swap", "Swap %", COLORS["swap"])],
            y_label="%", window_sec=self.settings.graph_window_sec
        )
        self._graphs["ram"] = g_ram
        box.pack_start(g_ram, True, True, 0)
        return box

    # ── History tab ─────────────────────────────────────────────────────────

    def _build_history_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)

        label = Gtk.Label(
            label="Last 24 hours — data sampled every ~5 s", xalign=0.0
        )
        label.get_style_context().add_class("unit")
        box.pack_start(label, False, False, 0)

        g_hist = RollingGraph(
            "Historical Overview",
            [("cpu", "CPU %", COLORS["cpu"]),
             ("ram", "RAM %", COLORS["ram"]),
             ("gpu", "GPU %", COLORS["gpu"])],
            y_label="%", window_sec=86400, height_px=220
        )
        self._graphs["history"] = g_hist
        box.pack_start(g_hist, True, True, 0)

        self._reload_history_btn = Gtk.Button(label="Reload history")
        self._reload_history_btn.connect("clicked", lambda *_: self._load_history())
        self._reload_history_btn.set_halign(Gtk.Align.START)
        self._reload_history_btn.set_margin_start(4)
        box.pack_start(self._reload_history_btn, False, False, 0)
        return box

    def _load_history(self):
        rows = self.history.fetch(seconds=86400)
        g = self._graphs.get("history")
        if not g:
            return
        for row in rows:
            ts, cpu, cpu_t, ram, gpu, gpu_t = row
            g.push(ts, {"cpu": cpu, "ram": ram, "gpu": gpu})
        g.redraw()

    # ── Overview: top processes ──────────────────────────────────────────────

    def _refresh_overview_procs(self) -> bool:
        import threading
        threading.Thread(target=self._fetch_overview_procs, daemon=True).start()
        return True  # keep GLib timer

    def _fetch_overview_procs(self):
        from .processes import collect_top_processes
        groups = collect_top_processes(n=5, sort_by="cpu")
        GLib.idle_add(self._render_overview_procs, groups)

    def _render_overview_procs(self, groups):
        for child in self._ov_top_inner.get_children():
            self._ov_top_inner.remove(child)
        for g in groups:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            # name
            name_lbl = Gtk.Label(xalign=0.0)
            name_lbl.set_markup(f'<span color="#cdd6f4" weight="bold">{g.name}</span>')
            name_lbl.set_size_request(180, -1)
            name_lbl.set_ellipsize(3)  # END
            row.pack_start(name_lbl, False, False, 0)
            # desc
            if g.description:
                desc_lbl = Gtk.Label(label=g.description, xalign=0.0)
                desc_lbl.get_style_context().add_class("unit")
                desc_lbl.set_size_request(120, -1)
                desc_lbl.set_ellipsize(3)
                row.pack_start(desc_lbl, False, False, 0)
            row.pack_start(Gtk.Box(), True, True, 0)
            # cpu
            cpu_color = "#f38ba8" if g.cpu_percent >= 30 else "#fab387" if g.cpu_percent >= 10 else "#a6e3a1"
            cpu_lbl = Gtk.Label(xalign=1.0)
            cpu_lbl.set_markup(f'<span color="{cpu_color}" font_family="monospace">{g.cpu_percent:5.1f}%</span>')
            row.pack_start(cpu_lbl, False, False, 0)
            # ram
            ram_text = f"{g.ram_mb/1024:.1f}GB" if g.ram_mb >= 1024 else f"{g.ram_mb:.0f}MB"
            ram_lbl = Gtk.Label(xalign=1.0)
            ram_lbl.set_markup(f'<span color="#cba6f7" font_family="monospace">  {ram_text}</span>')
            row.pack_start(ram_lbl, False, False, 0)
            self._ov_top_inner.pack_start(row, False, False, 0)
            row.show_all()

    # ── Settings ────────────────────────────────────────────────────────────

    def _open_settings(self, *_):
        open_settings_dialog(self.settings, parent=self)

    # ── Data update ─────────────────────────────────────────────────────────

    def _on_stats(self, s: SystemStats):
        GLib.idle_add(self._update_ui, s)
        now = time.time()
        if now - self._last_history_record >= 5.0:
            self.history.record(s)
            self._last_history_record = now
            # push to history graph
            g = self._graphs.get("history")
            if g:
                g.push(s.timestamp, {
                    "cpu": s.cpu_percent, "ram": s.ram_percent, "gpu": s.gpu_percent
                })

    def _update_ui(self, s: SystemStats):
        # Overview - CPU
        self._ov_cpu_pct.set_text(f"{s.cpu_percent:.1f}%")
        _update_pbar(self._ov_cpu_pb, s.cpu_percent)
        if s.cpu_temp > 0:
            self._ov_cpu_temp.set_text(f"{s.cpu_temp:.0f}°C")
        if s.cpu_freq_mhz > 0:
            self._ov_cpu_freq.set_text(
                f"{s.cpu_freq_mhz:.0f} / {s.cpu_freq_max_mhz:.0f} MHz"
            )
        self._ov_throttle.set_text("⚡ Thermal throttling active!" if s.thermal_throttling else "")

        # Overview - GPU
        if s.gpu_available:
            self._ov_gpu_frame.set_visible(True)
            self._ov_gpu_name.set_text(s.gpu_name)
            self._ov_gpu_pct.set_text(f"{s.gpu_percent:.1f}%")
            _update_pbar(self._ov_gpu_pb, s.gpu_percent)
            self._ov_gpu_temp.set_text(f"{s.gpu_temp:.0f}°C" if s.gpu_temp else "--")
            self._ov_gpu_vram.set_text(
                f"{s.gpu_mem_used_mb/1024:.1f} / {s.gpu_mem_total_mb/1024:.1f} GB"
                f"  ({s.gpu_mem_percent:.0f}%)"
            )
            self._ov_gpu_power.set_text(
                f"{s.gpu_power_w:.1f} W" if s.gpu_power_w > 0 else "--"
            )
            self._gpu_tab_label.set_text(s.gpu_name)
        else:
            self._ov_gpu_frame.set_visible(False)

        # Overview - RAM
        self._ov_ram_pct.set_text(f"{s.ram_percent:.1f}%")
        _update_pbar(self._ov_ram_pb, s.ram_percent)
        self._ov_ram_detail.set_text(f"{s.ram_used_gb:.2f} / {s.ram_total_gb:.2f} GB")
        self._ov_swap.set_text(
            f"{s.swap_used_gb:.2f} / {s.swap_total_gb:.2f} GB  ({s.swap_percent:.0f}%)"
        )

        # Warnings
        for child in self._ov_warn_inner.get_children():
            self._ov_warn_inner.remove(child)
        if s.warnings:
            self._ov_warn_frame.set_visible(True)
            for w in s.warnings:
                lbl = Gtk.Label(label=f"⚠  {w}", xalign=0.0)
                lbl.get_style_context().add_class("metric-warn")
                self._ov_warn_inner.pack_start(lbl, False, False, 0)
                lbl.show()
        else:
            self._ov_warn_frame.set_visible(False)

        # CPU per-core bars
        for i, (pb, pct) in enumerate(zip(self._core_bars, s.cpu_per_core)):
            _update_pbar(pb, pct)
            self._core_labels[i].set_text(f"C{i}\n{pct:.0f}%")

        # Push to rolling graphs
        t = s.timestamp
        if "cpu_usage" in self._graphs:
            self._graphs["cpu_usage"].push(t, {"cpu": s.cpu_percent})
        if "cpu_temp" in self._graphs:
            self._graphs["cpu_temp"].push(t, {"cpu_temp": s.cpu_temp})
        if "gpu_usage" in self._graphs:
            self._graphs["gpu_usage"].push(t, {
                "gpu": s.gpu_percent, "gpu_mem": s.gpu_mem_percent
            })
        if "gpu_temp" in self._graphs:
            self._graphs["gpu_temp"].push(t, {"gpu_temp": s.gpu_temp})
        if "ram" in self._graphs:
            self._graphs["ram"].push(t, {"ram": s.ram_percent, "swap": s.swap_percent})

    def _redraw_graphs(self):
        for g in self._graphs.values():
            g.redraw()
        return True  # keep timer running
