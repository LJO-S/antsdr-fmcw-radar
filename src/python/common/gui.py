import sys
import time
import numpy as np
import dataclasses
from collections import defaultdict
from pathlib import Path
from PySide6.QtCore import QRectF, Qt, QLocale, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QFormLayout,
    QCheckBox,
    QSpinBox,
    QLineEdit,
    QGroupBox,
    QPushButton,
    QToolBar,
    QLabel,
    QSlider,
    QComboBox,
    QFileDialog,
    QStyle,
    QSizePolicy,
)
from PySide6.QtGui import QBrush, QColor, QDoubleValidator, QKeySequence
import pyqtgraph as pg

import common.dsp as dsp


class RadarDisplay(QMainWindow):
    """
    Tab 0 - Radar:
    ┌─────────────────────────┬─────────────────────────┐
    │  Up-chirp RD map        │ Detections │            │
    │                         │  (r, v)    │ Detections │
    ├─────────────────────────┼────────────┤    List    │
    │  Down-chirp RD map      │ Sector-PPI │            │
    │                         │ x-y (TDM)  │            │
    │                         ├─────────────────────────┤
    │                         │ [] Up-down detections   │
    │                         │ [] MTI                  │
    │                         │ [] Record [notes      ] │
    └─────────────────────────┴─────────────────────────┘

    Tab 1 - History (last N s, detections as dots):
    ┌─────────────────────────────────────────┐
    │  Range vs time (RTI)                    │
    ├─────────────────────────────────────────┤
    │  Velocity vs time                       │
    └─────────────────────────────────────────┘

    Tab 2 - Signals:
    ┌─────────────────────────────────────────┐
    │  RX spectrogram                         │
    ├─────────────────────────────────────────┤
    │  IF spectrogram                         │
    └─────────────────────────────────────────┘

    Tab 3 - Config:
    ┌─────────────────────────┬─────────────┐
    │  Tx Spectrogram         │ Cfg Params  │
    ├─────────────────────────├─────────────┤
    │  Radar Parameters       │ Cfg Button  │
    └─────────────────────────┴─────────────┘

    Intake and draw split: update(), update_signals(), update_recorded() only keep
    newest data (feed History buffers every CPI) and mark dirty. One 33 ms timer draws
    current tab when dirty; tab change draws new tab at once; draw_now() = one full
    draw without timer. Frames held by reference until drawn, so callers pass fresh
    arrays per CPI (process_cpi does).
    """

    reconfigure_requested = Signal(object)
    fake_targets_changed = Signal(object)
    mti_signal_changed = Signal(bool)
    record_changed = Signal(bool)
    record_notes_changed = Signal(str)

    FAKE_TGT_COLUMNS = [
        ("r0", "Range [m]", 500.0),
        ("v0", "Vel [m/s]", 0.0),
        ("a0", "Accel [m/s2]", 0.0),
        ("theta0", "Azimuth [deg]", 0.0),
        ("duration", "Duration [s]", 10.0),
        ("amp", "Amp", 0.1),
    ]

    # History tab: image columns = fixed time bins (strongest CPI wins), so varying
    # CPI rate cannot stretch time axis. Images redraw <= 5 Hz, data taken every CPI
    HISTORY_WINDOWS_S = [10, 30, 60, 120]
    HISTORY_BIN_S = 0.1
    HISTORY_REDRAW_S = 0.2
    HISTORY_FLOOR_DB = -80.0  # RD maps' display floor

    # One timer draws current tab; detection table redraws <= 5 Hz
    DRAW_INTERVAL_MS = 33
    TABLE_REDRAW_S = 0.2

    # Detections faster than this coloured receding (v > 0) or approaching
    MOVING_V_MS = 5.0
    # Sector-PPI: fan drawn, spoke spacing inside it
    XY_FOV_DEG = 60
    XY_SPOKE_DEG = 30

    def __init__(self, a_config):
        super().__init__()
        self.setWindowTitle("FMCW Radar")

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self._config = a_config
        # Intake state: newest frame / spectrograms, drawn by timer when dirty
        self._frame = None  # (up, down, ranges, velocities, targets)
        self._radar_dirty = False
        self._table_dirty = False
        self._table_last = 0.0
        self._spec_rx = None  # pending (spec) per plot, None once drawn
        self._spec_if = None
        self._spec_axes = None  # (t, f)
        # Detection table items, one row of 5 per pool slot, kept for reuse
        self._table_pool = []
        self._table_state = []  # velocity cell colour per pool row: -1, 0, 1
        self._table_fg = QBrush(QColor("white"))
        self._table_bg = {
            1: QBrush(QColor("#25415c")),
            -1: QBrush(QColor("#5c2a2a")),
            0: QBrush(),
        }
        # --------- Tab 0: Radar ---------
        radar_tab = QWidget()
        self._radar_tab = radar_tab
        tabs.addTab(radar_tab, "Radar")
        radar_layout = QHBoxLayout(radar_tab)

        # ---------------------------------
        # A. Left column = up/down RD maps
        # ---------------------------------
        left_col = QVBoxLayout()
        radar_layout.addLayout(left_col, stretch=2)

        self.rd_up_plot = pg.PlotWidget(title="Up-chirp")
        self.rd_up_plot.setLabel("left", "Range", units="m")
        self.rd_up_plot.setLabel("bottom", "Velocity", units="m/s")
        self.rd_up_image = pg.ImageItem()
        self.rd_up_image.setColorMap(pg.colormap.get("CET-L9"))
        self.rd_up_plot.addItem(self.rd_up_image)
        left_col.addWidget(self.rd_up_plot)

        self.rd_down_plot = pg.PlotWidget(title="Down-chirp")
        self.rd_down_plot.setLabel("left", "Range", units="m")
        self.rd_down_plot.setLabel("bottom", "Velocity", units="m/s")
        self.rd_down_image = pg.ImageItem()
        self.rd_down_image.setColorMap(pg.colormap.get("CET-L9"))
        self.rd_down_plot.addItem(self.rd_down_image)
        left_col.addWidget(self.rd_down_plot)

        # ---------------------------------
        # B. Middle column = detections scatter + toggle + MTI
        # ---------------------------------
        middle_col = QVBoxLayout()
        radar_layout.addLayout(middle_col, stretch=1)

        self.det_plot = pg.PlotWidget(title="Detections")
        self.det_plot.setLabel("left", "Range", units="m")
        self.det_plot.setLabel("bottom", "Velocity", units="m/s")
        self.det_plot.setBackground("#0a1628")
        self.det_plot.showGrid(x=True, y=True, alpha=0.3)
        middle_col.addWidget(self.det_plot)

        # Sector-PPI (plan position indicator), TDM only (set_config):
        # x = r sin(theta), y = r cos(theta)
        self.xy_plot = pg.PlotWidget(title="Sector-PPI")
        # No y axis: bottom ticks label each range ring where it meets fan edge
        self.xy_plot.hideAxis("left")
        self.xy_plot.setLabel("bottom", "Range", units="m")
        self.xy_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.xy_plot.setBackground("#0a1628")
        self.xy_plot.setAspectLocked(True)
        # Range rings and spokes, one NaN-separated curve, rebuilt per config
        self.xy_grid = pg.PlotCurveItem(
            pen=pg.mkPen((255, 255, 255, 60), width=1), connect="finite"
        )
        self.xy_plot.addItem(self.xy_grid)
        self._xy_angle_labels = []  # one TextItem per spoke, rebuilt with the grid
        self.xy_xlines = pg.PlotCurveItem(
            pen=pg.mkPen((255, 255, 255, 70), width=1, style=Qt.DotLine),
            connect="finite",
        )
        self.xy_plot.addItem(self.xy_xlines, ignoreBounds=True)
        self.xy_scatter = pg.ScatterPlotItem(pen=pg.mkPen(None))
        self.xy_plot.addItem(self.xy_scatter)
        self._xy_brushes = {
            "receding": pg.mkBrush("#4da3ff"),
            "approaching": pg.mkBrush("#ff6b6b"),
            "slow": pg.mkBrush("yellow"),
        }
        middle_col.addWidget(self.xy_plot)

        self.xs_toggle = QCheckBox("Up/Down Detections")
        self.xs_toggle.setChecked(False)
        self.xs_toggle.toggled.connect(self._mark_radar_dirty)
        middle_col.addWidget(self.xs_toggle)

        self.scatter_both = pg.ScatterPlotItem(
            size=10, symbol="o", pen=pg.mkPen(None), brush=pg.mkBrush("yellow")
        )
        self.scatter_up = pg.ScatterPlotItem(
            size=8, symbol="x", pen=pg.mkPen((255, 255, 255, 100), width=2)
        )
        self.scatter_down = pg.ScatterPlotItem(
            size=8, symbol="x", pen=pg.mkPen((0, 255, 255, 100), width=2)
        )
        self.det_plot.addItem(self.scatter_both)
        self.det_plot.addItem(self.scatter_up)
        self.det_plot.addItem(self.scatter_down)
        # Playback only: live-recorded detections, rings around replayed dots.
        # Drawn last (on top), hidden until update_recorded()
        self.scatter_recorded = pg.ScatterPlotItem(
            size=16,
            symbol="o",
            pen=pg.mkPen((255, 0, 255), width=2),
            brush=pg.mkBrush(None),
        )
        self.det_plot.addItem(self.scatter_recorded)
        self.scatter_recorded.setVisible(False)
        self._recorded = []
        self._show_recorded = False

        self.mti_en_box = QCheckBox("MTI")
        self.mti_en_box.setChecked(a_config.MTI_EN)
        middle_col.addWidget(self.mti_en_box)
        self.mti_en_box.toggled.connect(self.mti_signal_changed)

        # Record: worker owns session, on_recording() mirrors state back
        record_row = QHBoxLayout()
        self.record_box = QCheckBox("Record")
        self.record_notes = QLineEdit()
        self.record_notes.setPlaceholderText(
            "Session notes (read when a session opens)"
        )
        record_row.addWidget(self.record_box)
        record_row.addWidget(self.record_notes, stretch=1)
        middle_col.addLayout(record_row)
        self.record_box.toggled.connect(self.record_changed)
        # Every edit, so later-opened session (RE-CONFIGURE, target edit) gets current
        # text, not text from Record tick
        self.record_notes.textChanged.connect(self.record_notes_changed)

        # ---------------------------------
        # C. Right column = detections list
        # ---------------------------------
        self.det_table = QTableWidget(0, 5)
        self.det_table.setHorizontalHeaderLabels(
            ["#", "Range [m]", "Velocity [m/s]", "Azimuth [deg]", "RCS [m²]"]
        )
        self.det_table.verticalHeader().setVisible(False)
        self.det_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.det_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        radar_layout.addWidget(self.det_table, stretch=1)

        # --------- Tab 1: History ---------
        self._build_history_tab(tabs)

        # --------- Tab 2: Signals ---------
        signals_tab = QWidget()
        self._signals_tab = signals_tab
        tabs.addTab(signals_tab, "Signals")
        signals_layout = QVBoxLayout(signals_tab)

        for title, attr in [("RX", "rx"), ("IF", "if")]:
            plot = pg.PlotWidget(title=f"{title} Instantaneuous Freq")
            plot.setLabel("left", "Frequency", units="Hz")
            plot.setLabel("bottom", "Time", units="s")
            image = pg.ImageItem()
            image.setColorMap(pg.colormap.get("CET-L9"))
            plot.addItem(image)
            setattr(self, f"{attr}_spec_plot", plot)
            setattr(self, f"{attr}_spec_image", image)
            signals_layout.addWidget(plot)

        # --------- Tab 3: Config ---------
        config_tab = QWidget()
        tabs.addTab(config_tab, "Configuration")
        config_layout = QHBoxLayout(config_tab)
        # ---  Tx Chirps & Radar Parameters ---
        config_layout_left_col = QVBoxLayout()

        # -- Tx Freq vs Time --
        plot = pg.PlotWidget(title="Tx Instantaneous Frequency")
        plot.setLabel("left", "Frequency", units="Hz")
        plot.setLabel("bottom", "Time", units="s")
        self.tx_curve = plot.plot(pen="y")
        self.tx_curve.setDownsampling(auto=False)
        self.tx_curve.setClipToView(False)
        setattr(self, "tx_spec_plot", plot)
        config_layout_left_col.addWidget(plot)

        # -- Show Radar Params --
        box = QGroupBox("Radar Characteristics")
        param_box = QVBoxLayout(box)
        self.param_widget = QTableWidget(0, 3)
        self.param_widget.setHorizontalHeaderLabels(["Name", "Value", "Unit"])
        self.param_widget.verticalHeader().setVisible(False)
        self.param_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.param_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        param_box.addWidget(self.param_widget)
        config_layout_left_col.addWidget(box)

        config_layout.addLayout(config_layout_left_col, stretch=1)

        # --- Configuration ---
        cfg_box = QGroupBox("Configuration")
        config_layout_right_col = QVBoxLayout(cfg_box)

        # Create scroll
        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        # Form layout inside a widget
        form_widget = QWidget()
        form_layout = QFormLayout(form_widget)  # this is used by commands
        # Set Widget to scroller
        config_scroll.setWidget(form_widget)
        # Add widget
        config_layout_right_col.addWidget(config_scroll)

        self._cfg_form_widget = form_widget  # set_playback_mode() disables it

        # Create Fake Targets section
        fake_target_box = QGroupBox("Fake Target Simulation")
        self.fake_target_box = fake_target_box
        fake_targets_layout = QVBoxLayout(fake_target_box)
        # Targets
        self.fake_targets_table = QTableWidget(0, 6)
        self.fake_targets_table.setHorizontalHeaderLabels(
            [c[1] for c in self.FAKE_TGT_COLUMNS]
        )
        self.fake_targets_table.verticalHeader().setVisible(False)
        self.fake_targets_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.fake_targets_table.setSelectionBehavior(QTableWidget.SelectRows)
        fake_targets_layout.addWidget(self.fake_targets_table)
        # +/- buttons
        fake_targets_button_layout = QHBoxLayout()
        fake_target_add_button = QPushButton("+")
        fake_target_del_button = QPushButton("-")
        fake_targets_button_layout.addWidget(fake_target_add_button)
        fake_targets_button_layout.addWidget(fake_target_del_button)
        fake_targets_layout.addLayout(fake_targets_button_layout)
        config_layout_right_col.addWidget(fake_target_box)

        # Connect fake target widgets
        fake_target_add_button.clicked.connect(self._fake_target_add_row)
        fake_target_del_button.clicked.connect(self._fake_target_del_row)
        self.fake_targets_table.itemChanged.connect(self._fake_target_edited)

        # Create groups containing the Config parameters
        groups = defaultdict(list)
        for field in dataclasses.fields(a_config):
            if not field.metadata:
                continue
            groups[field.metadata["group"]].append(field)

        # Create helper functions
        def _make_widget(a_field):
            if a_field.type is bool:
                w = QCheckBox()
            elif a_field.type is int:
                w = QSpinBox()
                w.setRange(-1_000_000, 1_000_000)
            elif a_field.type is float:
                w = QLineEdit()
                validator = QDoubleValidator(
                    bottom=-1_000_000, top=1_000_000, decimals=10
                )
                validator.setNotation(QDoubleValidator.ScientificNotation)
                validator.setLocale(QLocale.c())
                w.setValidator(validator)
            else:
                # string
                w = QLineEdit()
            if a_field.metadata.get("readonly"):
                w.setEnabled(False)
            return w

        self._cfg_widgets = {}
        self._cfg_fields = {}
        for group_name, group_fields in groups.items():
            box = QGroupBox(group_name.upper())
            box_form = QFormLayout(box)
            for f in group_fields:
                widget = _make_widget(a_field=f)
                self._cfg_widgets[f.name] = widget
                self._cfg_fields[f.name] = f
                unit = f.metadata.get("unit", "")
                label = (
                    f'{f.metadata["label"]} [{unit}]' if unit else f.metadata["label"]
                )
                box_form.addRow(label, widget)
            form_layout.addRow(box)

        # Grey out
        loopback_names = [
            f.name
            for f in self._cfg_fields.values()
            if f.metadata["group"] == "loopback"
        ]

        loopback_ctrl = self._cfg_widgets["SDR_LOOPBACK_EN"]

        def _apply_loopback(a_on):
            for n in loopback_names:
                self._cfg_widgets[n].setEnabled(a_on)

        loopback_ctrl.toggled.connect(_apply_loopback)
        _apply_loopback(loopback_ctrl.isChecked())

        # Create RE-CONFIGURE button
        self.re_cfg_button = QPushButton("RE-CONFIGURE")
        self.re_cfg_button.clicked.connect(
            self.reconfigure
        )  # connect method (no parentheses)
        self.re_cfg_button.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white;"
            " font-weight: bold; padding: 6px; }"
            "QPushButton:disabled { background-color: #555555; color: #aaaaaa; }"
        )
        config_layout_right_col.addWidget(self.re_cfg_button)

        config_layout.addWidget(cfg_box, stretch=1)

        # Setup GUI
        self.set_config(a_config)

        self._draw_timer = QTimer(self)
        self._draw_timer.setInterval(self.DRAW_INTERVAL_MS)
        self._draw_timer.timeout.connect(self._on_tick)
        self._draw_timer.start()

    def reconfigure(self):
        values = {}
        for name, f in self._cfg_fields.items():
            if f.metadata.get("readonly"):
                continue
            try:
                values[name] = self.read_cfg_reg(f)
                self._cfg_widgets[name].setStyleSheet("")
            except ValueError:
                self._cfg_widgets[name].setStyleSheet("border: 1px solid red")
                return
        new_cfg = dataclasses.replace(self._config, **values)
        self.re_cfg_button.setEnabled(False)  # Disable button until ACK
        self.reconfigure_requested.emit(new_cfg)
        self.set_config(new_cfg)

    def on_reconfigure_done(self, a_ok, a_config):
        self.re_cfg_button.setEnabled(True)
        if not a_ok:
            self.set_config(a_config)

    def on_recording(self, a_session_dir):
        """Worker's session state: directory, or None when not recording (or disk
        error stopped it). Does not re-emit record_changed."""
        self.record_box.blockSignals(True)
        self.record_box.setChecked(a_session_dir is not None)
        self.record_box.blockSignals(False)
        if a_session_dir is None:
            self.record_box.setText("Record")
            self.record_box.setToolTip("")
        else:
            self.record_box.setText(f"Record ({a_session_dir.name})")
            self.record_box.setToolTip(str(a_session_dir))

    # ---------------------------------
    # Playback (offline.playback)
    # ---------------------------------
    def set_playback_mode(self):
        """Session owns config and fake targets: both read-only, RE-CONFIGURE hidden,
        no recording, status bar says not live. MTI box only shows what each CPI ran
        with (player bar overrides)."""
        self.setWindowTitle("FMCW Radar - Playback")
        self.record_box.hide()
        self.record_notes.hide()
        self.mti_en_box.setEnabled(False)
        # Form, not scroll area, so long form still scrolls
        self._cfg_form_widget.setEnabled(False)
        self.fake_target_box.setEnabled(False)
        self.re_cfg_button.hide()

        badge = QLabel(" PLAYBACK - not live ")
        badge.setStyleSheet(
            "QLabel { background-color: #b26a00; color: white; font-weight: bold; }"
        )
        self.statusBar().addWidget(badge)
        self.status_label = QLabel("Open a session (Ctrl+O)")
        self.statusBar().addWidget(self.status_label, stretch=1)

    def set_status(self, a_text, a_color=None):
        self.status_label.setText(a_text)
        style = f"QLabel {{ color: {a_color}; }}" if a_color else ""
        self.status_label.setStyleSheet(style)

    def show_fake_targets(self, a_targets):
        """Fill the fake-target table without emitting fake_targets_changed."""
        t = self.fake_targets_table
        t.blockSignals(True)
        t.setRowCount(0)
        for row, target in enumerate(a_targets):
            t.insertRow(row)
            for col, (key, header, default) in enumerate(self.FAKE_TGT_COLUMNS):
                t.setItem(row, col, QTableWidgetItem(str(target.get(key, default))))
        t.blockSignals(False)

    def show_mti(self, a_on):
        """Mirror the MTI a CPI ran with, without emitting mti_signal_changed."""
        self.mti_en_box.blockSignals(True)
        self.mti_en_box.setChecked(a_on)
        self.mti_en_box.blockSignals(False)

    def update_recorded(self, a_targets):
        """Detections recorded live for this CPI (None: no recorded line), drawn as
        rings with frame. Same kind filter as update()."""
        self._recorded = a_targets or []
        self._radar_dirty = True

    def _draw_recorded(self):
        show_xs = self.xs_toggle.isChecked()
        pos = [
            (t["v"], t["r"])
            for t in self._recorded
            if not self._config.TRIANGLE_EN or t["kind"] == "both" or show_xs
        ]
        self.scatter_recorded.setData(pos=pos or [(0, 0)], size=16)
        self.scatter_recorded.setVisible(self._show_recorded and len(pos) > 0)

    def set_recorded_visible(self, a_on):
        self._show_recorded = a_on
        self.update_recorded(self._recorded)

    def read_cfg_reg(self, a_field):
        if a_field.type is bool:
            w = self._cfg_widgets[a_field.name].isChecked()
        elif a_field.type is int:
            w = self._cfg_widgets[a_field.name].value()
        elif a_field.type is float:
            w = float(self._cfg_widgets[a_field.name].text()) * a_field.metadata.get(
                "scale", 1
            )
        else:
            # string
            w = self._cfg_widgets[a_field.name].text()
        return w

    def write_cfg_reg(self, a_field, a_value):
        w = self._cfg_widgets[a_field.name]
        if a_field.type is bool:
            w.setChecked(a_value)
        elif a_field.type is int:
            w.setValue(a_value)
        elif a_field.type is float:
            w.setText(str(a_value / a_field.metadata.get("scale", 1)))
        else:
            w.setText(str(a_value))

    def set_detection_limits(self, r_min, r_max, v_min, v_max):
        self.det_plot.setXRange(v_min, v_max, padding=0)
        self.det_plot.setYRange(r_min, r_max, padding=0)
        self.det_plot.setLimits(
            xMin=v_min,
            xMax=v_max,
            yMin=r_min,
            yMax=r_max,
            minXRange=v_max - v_min,
            maxXRange=v_max - v_min,
            minYRange=r_max - r_min,
            maxYRange=r_max - r_min,
        )

    def _mark_radar_dirty(self, *_args):
        self._radar_dirty = True

    def update(
        self, a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets, a_t=None
    ):
        """Intake only: keeps frame (by reference, see class docstring), feeds History
        buffers; timer draws. a_t: CPI time in s (playback passes recorded one);
        None = now (live app)."""
        self._frame = (a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets)
        self._radar_dirty = True
        self._table_dirty = True
        self._update_history(
            time.monotonic() if a_t is None else a_t,
            a_rd_up_db,
            a_ranges,
            a_velocities,
            a_targets,
        )

    # ---------------------------------
    # Drawing (timer, tab change, draw_now)
    # ---------------------------------
    def _on_tick(self):
        self._draw_current(a_now=False)

    def draw_now(self):
        """One full draw of current tab, ignoring timer and throttles. For tests and
        profiler."""
        self._radar_dirty = True
        self._table_dirty = True
        self._hist_dirty = True
        self._draw_current(a_now=True)

    def _draw_current(self, a_now):
        """Draw tab on screen if anything new. a_now ignores 5 Hz throttles (tab
        change, draw_now). Configuration has nothing per frame."""
        tab = self._tabs.currentWidget()
        if tab is self._radar_tab:
            self._draw_radar(a_now)
        elif tab is self._history_tab:
            self._draw_history_tab(a_now)
        elif tab is self._signals_tab:
            self._draw_signals()

    def _draw_radar(self, a_now):
        if self._radar_dirty:
            self._radar_dirty = False
            if self._frame is not None:
                self._draw_rd_and_dets(*self._frame)
            self._draw_recorded()
        if self._table_dirty and self._frame is not None:
            now = time.monotonic()
            if a_now or now - self._table_last >= self.TABLE_REDRAW_S:
                self._table_dirty = False
                self._table_last = now
                self._draw_table(self._frame[4])

    def _draw_rd_and_dets(
        self, a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets
    ):
        # -------------------------
        # RD maps
        # -------------------------
        rect_up = QRectF(
            float(a_velocities[0]),
            float(a_ranges[0]),
            float(a_velocities[-1] - a_velocities[0]),
            float(a_ranges[-1] - a_ranges[0]),
        )

        self.rd_up_image.setImage(a_rd_up_db, levels=(-80, 0))
        self.rd_up_image.setRect(rect_up)

        if a_rd_down_db is not None:
            rect_down = QRectF(
                float(a_velocities[-1]),
                float(a_ranges[0]),
                float(a_velocities[0] - a_velocities[-1]),
                float(a_ranges[-1] - a_ranges[0]),
            )
            self.rd_down_image.setImage(a_rd_down_db, levels=(-80, 0))
            self.rd_down_image.setRect(rect_down)

        # -------------------------
        # Detections; UP/DOWN toggle
        # -------------------------
        show_xs = self.xs_toggle.isChecked()
        both = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "both"]
        up = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "up"]
        down = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "down"]

        if a_rd_down_db is None:
            both = up
            up, down = [], []

        self.scatter_both.setData(pos=both or [(0, 0)], size=10)
        self.scatter_up.setData(pos=up or [(0, 0)], size=8)
        self.scatter_down.setData(pos=down or [(0, 0)], size=8)

        self.scatter_both.setVisible(len(both) > 0)
        self.scatter_up.setVisible(show_xs and len(up) > 0)
        self.scatter_down.setVisible(show_xs and len(down) > 0)

        if self._config.TDM_EN:
            self._update_xy(a_targets, show_xs)

    def _draw_table(self, a_targets):
        """Reuses items: text and velocity cell colour set on existing ones. Pool grows
        only when row count exceeds earlier max; shrinking takes items out of table
        (stay in pool)."""
        t = self.det_table
        n = len(a_targets)
        have = t.rowCount()
        if n < have:
            for row in range(n, have):
                for col in range(5):
                    t.takeItem(row, col)
            t.setRowCount(n)
        elif n > have:
            t.setRowCount(n)
            for row in range(have, n):
                if row == len(self._table_pool):
                    items = []
                    for _ in range(5):
                        item = QTableWidgetItem("")
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        item.setForeground(self._table_fg)
                        items.append(item)
                    self._table_pool.append(items)
                    self._table_state.append(0)
                for col, item in enumerate(self._table_pool[row]):
                    t.setItem(row, col, item)
        for i, tgt in enumerate(a_targets):
            theta = tgt.get("theta")
            theta_str = "-" if theta is None else f"{theta:+.1f}"
            row = (str(i), f'{tgt["r"]:.1f}', f'{tgt["v"]:.2f}', theta_str, "-")
            for item, text in zip(self._table_pool[i], row):
                if item.text() != text:
                    item.setText(text)
            state = (
                1
                if tgt["v"] > self.MOVING_V_MS
                else -1 if tgt["v"] < -self.MOVING_V_MS else 0
            )
            if self._table_state[i] != state:
                self._table_state[i] = state
                self._table_pool[i][2].setBackground(self._table_bg[state])

    # ---------------------------------
    # Sector-PPI (TDM)
    # ---------------------------------
    def _update_xy(self, a_targets, a_show_xs):
        """Detections with angle, same kind filter as (r, v) scatter. Dots for "both"
        (or sawtooth), crosses for single-ramp; colour by v."""
        tri = self._config.TRIANGLE_EN
        shown = [
            t
            for t in a_targets
            if t.get("theta") is not None
            and (not tri or t["kind"] == "both" or a_show_xs)
        ]
        r = np.array([t["r"] for t in shown], dtype=float)
        theta = np.radians([t["theta"] for t in shown])
        paired = [not tri or t["kind"] == "both" for t in shown]
        brushes = [
            self._xy_brushes[
                (
                    "receding"
                    if t["v"] > self.MOVING_V_MS
                    else "approaching" if t["v"] < -self.MOVING_V_MS else "slow"
                )
            ]
            for t in shown
        ]
        self.xy_scatter.setData(
            x=r * np.sin(theta),
            y=r * np.cos(theta),
            brush=brushes,
            symbol=["o" if p else "x" for p in paired],
            size=[10 if p else 8 for p in paired],
        )

    def _draw_xy_grid(self, a_max_range):
        """Range rings at a round step and spokes every XY_SPOKE_DEG across the fan."""
        step = 10 ** np.floor(np.log10(a_max_range))
        if a_max_range / step < 4:
            step /= 2
        rings = np.arange(step, a_max_range + 1e-9, step)
        fan = np.radians(np.linspace(-self.XY_FOV_DEG, self.XY_FOV_DEG, 121))
        xs, ys = [], []
        for r in rings:
            xs += [r * np.sin(fan), [np.nan]]
            ys += [r * np.cos(fan), [np.nan]]
        for label in self._xy_angle_labels:
            self.xy_plot.removeItem(label)
        self._xy_angle_labels = []
        for deg in range(-self.XY_FOV_DEG, self.XY_FOV_DEG + 1, self.XY_SPOKE_DEG):
            a = np.radians(deg)
            x, y = a_max_range * np.sin(a), a_max_range * np.cos(a)
            xs += [[0, x], [np.nan]]
            ys += [[0, y], [np.nan]]
            # Anchored on text side facing origin, so it sits outside arc
            label = pg.TextItem(
                f"{deg:+d}°" if deg else "0°",
                color=(255, 255, 255, 150),
                anchor=(0.5 - 0.5 * np.sin(a), 0.5 + 0.5 * np.cos(a)),
            )
            label.setPos(x, y)
            self.xy_plot.addItem(label, ignoreBounds=True)
            self._xy_angle_labels.append(label)
        self.xy_grid.setData(x=np.concatenate(xs), y=np.concatenate(ys))

        # Dotted verticals from below axis up to where each ring meets fan edge
        # (x = r sin(fov)); tick there labelled with ring range. Axis sits where
        # locked aspect puts view bottom, hence far start (item ignores bounds).
        fov = np.radians(self.XY_FOV_DEG)
        half_w = a_max_range * np.sin(fov)
        # Label every ring, or every second when crowded; rest get a tick
        every = 2 if len(rings) > 4 else 1
        xs, ys = [[0.0, 0.0], [np.nan]], [[-10 * a_max_range, 0.0], [np.nan]]
        major, minor = [(0.0, "0")], []
        for i, r in enumerate(rings):
            labelled = (i + 1) % every == 0
            for x in (-r * np.sin(fov), r * np.sin(fov)):
                xs += [[x, x], [np.nan]]
                ys += [[-10 * a_max_range, r * np.cos(fov)], [np.nan]]
                if labelled:
                    major.append((x, f"{r:g}"))
                else:
                    minor.append((x, ""))
        self.xy_xlines.setData(x=np.concatenate(xs), y=np.concatenate(ys))
        self.xy_plot.getAxis("bottom").setTicks([major, minor])

        # Side margin for outer ring tick label and edge angle labels
        self.xy_plot.setRange(
            xRange=(-1.25 * half_w, 1.25 * half_w),
            yRange=(0, a_max_range),
            padding=0,
        )

    # ---------------------------------
    # History tab
    # ---------------------------------
    def _build_history_tab(self, a_tabs):
        tab = QWidget()
        a_tabs.addTab(tab, "History")
        layout = QVBoxLayout(tab)
        # Images drawn only while tab current (see _draw_history_tab); showing it
        # draws at once, never stale
        self._history_tab = tab
        self._tabs = a_tabs

        row = QHBoxLayout()
        row.addWidget(QLabel("Window"))
        self.history_window_box = QComboBox()
        self.history_window_box.addItems([f"{s} s" for s in self.HISTORY_WINDOWS_S])
        self.history_window_box.setCurrentIndex(1)  # 30 s
        self.history_window_box.currentIndexChanged.connect(
            lambda _: self._reset_history()
        )
        row.addWidget(self.history_window_box)
        row.addWidget(
            QLabel("Strongest up-chirp cell per range bin (top), per velocity bin")
        )
        row.addStretch(1)
        layout.addLayout(row)

        def _plot(a_title, a_label, a_units):
            plot = pg.PlotWidget(title=a_title)
            plot.setLabel("left", a_label, units=a_units)
            plot.setLabel("bottom", "Time relative to the newest CPI", units="s")
            image = pg.ImageItem()
            image.setColorMap(pg.colormap.get("CET-L9"))
            plot.addItem(image)
            # Detections: a 0/1 mask on the image grid, transparent / yellow
            dets = pg.ImageItem()
            dets.setLookupTable(
                np.array([[0, 0, 0, 0], [255, 255, 0, 220]], dtype=np.uint8)
            )
            plot.addItem(dets)
            return plot, image, dets

        self.rti_plot, self.rti_image, self.rti_dets = _plot(
            "Range vs time", "Range", "m"
        )
        self.vti_plot, self.vti_image, self.vti_dets = _plot(
            "Velocity vs time", "Velocity", "m/s"
        )
        self.vti_plot.setXLink(self.rti_plot)
        layout.addWidget(self.rti_plot, stretch=3)
        layout.addWidget(self.vti_plot, stretch=2)
        self._reset_history()
        a_tabs.currentChanged.connect(self._on_tab_changed)

    def _reset_history(self):
        """Empty history. Next CPI sizes buffers (bin counts change with config)."""
        self._hist_window_s = self.HISTORY_WINDOWS_S[
            self.history_window_box.currentIndex()
        ]
        self._hist_rti = None  # (n_cols, n_range) dB, oldest column first
        self._hist_vti = None  # (n_cols, n_velocity)
        self._hist_bin = None  # time bin of the newest column
        self._hist_t = None  # time of the newest CPI
        self._hist_axes = None  # range/velocity axes buffers were built for
        self._hist_rdet = None  # (n_cols, n_range) uint8 detection mask, same grid
        self._hist_vdet = None  # (n_cols, n_velocity)
        self._hist_last_draw = 0.0
        self._hist_dirty = False
        self.rti_image.clear()
        self.vti_image.clear()
        self.rti_dets.clear()
        self.vti_dets.clear()
        # Window = time axis; auto-range would not follow image anyway
        self.rti_plot.enableAutoRange(x=False)
        self.rti_plot.setXRange(-self._hist_window_s, self.HISTORY_BIN_S, padding=0)

    def _update_history(self, a_t, a_rd_db, a_ranges, a_velocities, a_targets):
        axes = (
            len(a_ranges),
            float(a_ranges[0]),
            float(a_ranges[-1]),
            len(a_velocities),
            float(a_velocities[0]),
            float(a_velocities[-1]),
        )
        # Time backwards = playback seek; new axes = new config
        if self._hist_t is not None and (a_t < self._hist_t or axes != self._hist_axes):
            self._reset_history()
        n_cols = int(round(self._hist_window_s / self.HISTORY_BIN_S))
        new_bin = int(a_t // self.HISTORY_BIN_S)
        if self._hist_rti is None:
            self._hist_rti = np.full(
                (n_cols, len(a_ranges)), self.HISTORY_FLOOR_DB, np.float32
            )
            self._hist_vti = np.full(
                (n_cols, len(a_velocities)), self.HISTORY_FLOOR_DB, np.float32
            )
            self._hist_rdet = np.zeros((n_cols, len(a_ranges)), np.uint8)
            self._hist_vdet = np.zeros((n_cols, len(a_velocities)), np.uint8)
            self._hist_bin = new_bin
            self._hist_axes = axes
        shift = min(new_bin - self._hist_bin, n_cols)
        if shift > 0:
            for buf in (self._hist_rti, self._hist_vti):
                buf[:-shift] = buf[shift:].copy()
                buf[-shift:] = self.HISTORY_FLOOR_DB
            for buf in (self._hist_rdet, self._hist_vdet):
                buf[:-shift] = buf[shift:].copy()
                buf[-shift:] = 0
            self._hist_bin = new_bin
        # RD maps are (velocity, range): strongest cell per range and velocity bin,
        # max-held within newest time bin
        np.maximum(self._hist_rti[-1], a_rd_db.max(axis=0), out=self._hist_rti[-1])
        np.maximum(self._hist_vti[-1], a_rd_db.max(axis=1), out=self._hist_vti[-1])
        self._hist_t = a_t

        # Detections: nearest bin of uniform axes, marked in newest column
        if len(a_targets):
            rv = np.array([(t["r"], t["v"]) for t in a_targets], dtype=float)
            for vals, buf, (n, lo, hi) in (
                (rv[:, 0], self._hist_rdet, axes[0:3]),
                (rv[:, 1], self._hist_vdet, axes[3:6]),
            ):
                step = (hi - lo) / (n - 1) if n > 1 else 1.0
                idx = np.clip(np.rint((vals - lo) / step), 0, n - 1).astype(int)
                buf[-1, idx] = 1

        # Buffers fill every CPI; timer draws them (History tab only, <= 5 Hz)
        self._hist_dirty = True

    def _on_tab_changed(self, _index):
        # Shown tab never stale: draw what it missed at once
        self._draw_current(a_now=True)

    def _draw_history_tab(self, a_now):
        if not self._hist_dirty or self._hist_t is None:
            return
        now = time.monotonic()
        if a_now or now - self._hist_last_draw >= self.HISTORY_REDRAW_S:
            self._hist_dirty = False
            self._hist_last_draw = now
            self._draw_history()

    def _draw_history(self):
        # x = s relative to newest CPI; newest column ends where its time bin ends,
        # just right of 0
        x_right = (self._hist_bin + 1) * self.HISTORY_BIN_S - self._hist_t
        x_left = x_right - self._hist_window_s
        n_r, r0, r1, n_v, v0, v1 = self._hist_axes
        self.rti_image.setImage(self._hist_rti, levels=(self.HISTORY_FLOOR_DB, 0))
        self.rti_image.setRect(QRectF(x_left, r0, self._hist_window_s, r1 - r0))
        self.vti_image.setImage(self._hist_vti, levels=(self.HISTORY_FLOOR_DB, 0))
        self.vti_image.setRect(QRectF(x_left, v0, self._hist_window_s, v1 - v0))
        for item, mask, rect in (
            (self.rti_dets, self._hist_rdet, (r0, r1)),
            (self.vti_dets, self._hist_vdet, (v0, v1)),
        ):
            # one bin is under a pixel on screen: widen by 1 bin along range/velocity
            m = mask.copy()
            m[:, 1:] |= mask[:, :-1]
            m[:, :-1] |= mask[:, 1:]
            item.setImage(m, levels=(0, 1), autoLevels=False)
            item.setRect(QRectF(x_left, rect[0], self._hist_window_s, rect[1] - rect[0]))

    def update_signals(self, a_rx_spec, a_if_spec, a_t, a_f):
        """Intake only; drawn while Signals tab current. None keeps that plot's
        pending or drawn image."""
        if a_rx_spec is not None:
            self._spec_rx = a_rx_spec
        if a_if_spec is not None:
            self._spec_if = a_if_spec
        self._spec_axes = (a_t, a_f)

    def _draw_signals(self):
        if self._spec_rx is None and self._spec_if is None:
            return
        a_t, a_f = self._spec_axes
        rect = QRectF(
            float(a_t[0]),
            float(a_f[0]),
            float(a_t[-1] - a_t[0]),
            float(a_f[-1] - a_f[0]),
        )
        if self._spec_rx is not None:
            self.rx_spec_image.setImage(self._spec_rx, levels=(-80, 0))
            self.rx_spec_image.setRect(rect)
            self._spec_rx = None
        if self._spec_if is not None:
            self.if_spec_image.setImage(self._spec_if, levels=(-80, 0))
            self.if_spec_image.setRect(rect)
            self._spec_if = None

    def _fake_target_add_row(self):
        t = self.fake_targets_table
        t.blockSignals(True)
        row = t.rowCount()
        t.insertRow(row)

        for col, (key, header, default) in enumerate(self.FAKE_TGT_COLUMNS):
            t.setItem(row, col, QTableWidgetItem(str(default)))
        t.blockSignals(False)
        self._emit_fake_targets()

    def _fake_target_del_row(self):
        row = self.fake_targets_table.currentRow()
        if row < 0:
            return
        self.fake_targets_table.removeRow(row)
        self._emit_fake_targets()

    def _fake_target_edited(self):
        self._emit_fake_targets()

    def _emit_fake_targets(self):
        t = self.fake_targets_table
        fake_targets = []
        t.blockSignals(True)
        try:
            for row in range(self.fake_targets_table.rowCount()):
                params = {}
                for col, (key, header, default) in enumerate(self.FAKE_TGT_COLUMNS):
                    item = self.fake_targets_table.item(row, col)
                    try:
                        params[key] = float(item.text())
                        # reset to default
                        item.setBackground(QBrush())
                    except ValueError:
                        # mark bad cell
                        item.setBackground(QColor("#802020"))
                        return
                fake_targets.append(params)
        finally:
            t.blockSignals(False)
        self.fake_targets_changed.emit(fake_targets)

    def set_config(self, a_config):
        # Store new config
        self._config = a_config
        # Pending data shaped by old config (axes, map size): drop it. Recorded rings
        # go too, next draw clears them
        self._frame = None
        self._table_dirty = False
        self._spec_rx = self._spec_if = self._spec_axes = None
        self._recorded = []
        self._radar_dirty = True
        # Sawtooth never writes down map: no stale triangle one
        if not a_config.TRIANGLE_EN:
            self.rd_down_image.clear()
        # New config (or playback session) starts new history
        self._reset_history()

        # Angle only with TDM: Sector-PPI and azimuth column
        self.xy_plot.setVisible(a_config.TDM_EN)
        self.det_table.setColumnHidden(3, not a_config.TDM_EN)
        self.xy_scatter.setData(x=[], y=[])
        self._draw_xy_grid(a_config.MAX_RANGE)

        # Fabric IF mode: captured array is IF so hide IF spectrogram
        self.if_spec_plot.setVisible(not a_config.FABRIC_DECHIRP_EN)
        self.rx_spec_plot.setTitle(
            "RX (fabric IF) Instantaneuous Freq"
            if a_config.FABRIC_DECHIRP_EN
            else "RX Instantaneuous Freq"
        )

        # Update radar params
        radar_params = a_config.derived_params()
        # Flag when chirp, not MAX_RANGE_M, caps range axis
        range_short = a_config.EFFECTIVE_RANGE < a_config.MAX_RANGE_M
        self.param_widget.setRowCount(len(radar_params))  # Flush rows
        for i, (name, (value, unit)) in enumerate(radar_params.items()):
            flag = range_short and name == "Effective Range"
            for col, item in enumerate((name, f"{value:.1f}", unit)):
                table_item = QTableWidgetItem(item)
                table_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table_item.setForeground(QBrush(QColor("white")))
                if flag:
                    table_item.setBackground(QBrush(QColor("#5c2a2a")))
                    table_item.setToolTip(
                        f"Below Max Range ({a_config.MAX_RANGE_M:.0f} m): "
                        f"the range axis stops here"
                    )
                self.param_widget.setItem(i, col, table_item)

        # Populate widgets
        for name, f in self._cfg_fields.items():
            v = getattr(a_config, name)
            self.write_cfg_reg(a_field=f, a_value=v)

        # Store 2 chirp Tx curve
        tx_seq = np.tile(dsp.generate_chirp(a_config=a_config), 2)
        f = dsp.inst_freq(a_signal=tx_seq, a_radar_config=a_config)
        t = np.arange(len(tx_seq)) / a_config.FS
        self.tx_curve.setData(t[: len(f)], f)

        # Set detections limits
        self.set_detection_limits(
            r_min=0,
            r_max=a_config.MAX_RANGE,
            v_min=-a_config.MAX_VELOCITY,
            v_max=a_config.MAX_VELOCITY,
        )


class PlayerBar(QToolBar):
    """
    Playback transport (offline.playback), across the top of RadarDisplay:

    [Open] session "notes" | |< < > > | ====o======== block/time | speed | MTI | rings

    Emits requests only. Worker owns position, reports back via on_loaded /
    on_position / on_playing, so bar never runs ahead of data.
    Shortcuts: Ctrl+O open, Space play/pause, Left/Right step, Home first CPI.
    """

    open_requested = Signal(object)  # Path
    play_requested = Signal(bool)
    step_requested = Signal(int)
    seek_requested = Signal(int)
    speed_changed = Signal(float)  # 0 = as fast as possible
    mti_mode_changed = Signal(object)  # None = as recorded, else bool
    show_recorded_changed = Signal(bool)

    SPEEDS = [
        ("0.25x", 0.25),
        ("0.5x", 0.5),
        ("1x", 1.0),
        ("2x", 2.0),
        ("4x", 4.0),
        ("max", 0.0),
    ]
    MTI_MODES = [("MTI as recorded", None), ("MTI on", True), ("MTI off", False)]

    def __init__(self, a_start_dir):
        super().__init__("Playback")
        self.setMovable(False)
        self._start_dir = Path(a_start_dir)
        self._playing = False
        self._n_blocks = 0
        self._duration = 0.0
        style = self.style()

        open_action = self.addAction(
            style.standardIcon(QStyle.SP_DirOpenIcon), "Open session...", self._open
        )
        open_action.setShortcut(QKeySequence.Open)
        self.widgetForAction(open_action).setToolButtonStyle(
            Qt.ToolButtonTextBesideIcon
        )
        self.session_label = QLabel("No session")
        self.session_label.setMaximumWidth(360)
        self.session_label.setContentsMargins(6, 0, 6, 0)
        self.addWidget(self.session_label)
        self.addSeparator()

        def _action(a_icon, a_tip, a_key, a_slot):
            action = self.addAction(style.standardIcon(a_icon), a_tip, a_slot)
            action.setShortcut(QKeySequence(a_key))
            action.setToolTip(f"{a_tip} ({QKeySequence(a_key).toString()})")
            return action

        self._transport = [
            _action(
                QStyle.SP_MediaSkipBackward,
                "First CPI",
                Qt.Key_Home,
                lambda: self.seek_requested.emit(0),
            ),
            _action(
                QStyle.SP_MediaSeekBackward,
                "Step back",
                Qt.Key_Left,
                lambda: self.step_requested.emit(-1),
            ),
            _action(
                QStyle.SP_MediaPlay,
                "Play / pause",
                Qt.Key_Space,
                lambda: self.play_requested.emit(not self._playing),
            ),
            _action(
                QStyle.SP_MediaSeekForward,
                "Step forward",
                Qt.Key_Right,
                lambda: self.step_requested.emit(1),
            ),
        ]
        self._play_action = self._transport[2]

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimumWidth(200)
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Seek on release while dragging (each seek replays a CPI), at once on
        # groove click or key
        self.slider.valueChanged.connect(self._slider_changed)
        self.slider.sliderReleased.connect(
            lambda: self.seek_requested.emit(self.slider.value())
        )
        self.addWidget(self.slider)
        self.pos_label = QLabel()
        self.pos_label.setContentsMargins(6, 0, 6, 0)
        self.pos_label.setStyleSheet("QLabel { font-family: monospace; }")
        self.addWidget(self.pos_label)
        self.addSeparator()

        self.speed_box = QComboBox()
        self.speed_box.addItems([name for name, _ in self.SPEEDS])
        self.speed_box.setCurrentIndex(2)  # 1x
        self.speed_box.setToolTip("Playback speed, paced on the recorded CPI times")
        self.speed_box.currentIndexChanged.connect(
            lambda i: self.speed_changed.emit(self.SPEEDS[i][1])
        )
        self.addWidget(self.speed_box)

        self.mti_box = QComboBox()
        self.mti_box.addItems([name for name, _ in self.MTI_MODES])
        self.mti_box.setToolTip("Replay with the MTI each CPI ran with, or force it")
        self.mti_box.currentIndexChanged.connect(
            lambda i: self.mti_mode_changed.emit(self.MTI_MODES[i][1])
        )
        self.addWidget(self.mti_box)

        self.recorded_box = QCheckBox("Live detections (rings)")
        self.recorded_box.setChecked(True)
        self.recorded_box.setToolTip(
            "What the radar detected live, while this session was recorded, drawn as\n"
            "rings around the replayed detections (dots). A dot without a ring, or a\n"
            "ring without a dot, is where replay and the live run disagree."
        )
        self.recorded_box.toggled.connect(self.show_recorded_changed)
        self.addWidget(self.recorded_box)

        self._set_enabled(False)
        self._show_position(-1, 0.0)

    def _set_enabled(self, a_on):
        for w in self._transport + [self.slider, self.speed_box, self.mti_box]:
            w.setEnabled(a_on)

    def _open(self):
        path = QFileDialog.getExistingDirectory(
            self, "Open session", str(self._start_dir)
        )
        if path:
            self.open_requested.emit(Path(path))

    def _slider_changed(self, a_value):
        if not self.slider.isSliderDown():
            self.seek_requested.emit(a_value)

    def _show_position(self, a_block, a_t):
        # Fixed widths from session size, label does not jitter
        n_width = len(str(max(self._n_blocks, 1)))
        t_width = len(f"{self._duration:.1f}")
        self.pos_label.setText(
            f"{a_block + 1:>{n_width}}/{self._n_blocks}  "
            f"{a_t:>{t_width}.1f} / {self._duration:.1f} s"
        )

    def on_loaded(self, a_info):
        """a_info: offline.playback's session dict (dir, cfg, notes, ...)."""
        self._n_blocks = a_info["n_blocks"]
        self._duration = a_info["duration"]
        self._start_dir = a_info["dir"].parent
        notes = a_info["notes"]
        text = a_info["dir"].name + (f'  "{notes}"' if notes else "")
        metrics = self.session_label.fontMetrics()
        self.session_label.setText(
            metrics.elidedText(text, Qt.ElideRight, self.session_label.maximumWidth())
        )
        self.session_label.setToolTip(a_info["summary"])
        self.slider.blockSignals(True)
        self.slider.setRange(0, self._n_blocks - 1)
        self.slider.blockSignals(False)
        # New session replays with MTI it was recorded with
        self.mti_box.blockSignals(True)
        self.mti_box.setCurrentIndex(0)
        self.mti_box.blockSignals(False)
        self._set_enabled(True)

    def on_position(self, a_block, a_t):
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(a_block)
            self.slider.blockSignals(False)
        self._show_position(a_block, a_t)

    def on_playing(self, a_on):
        self._playing = a_on
        icon = QStyle.SP_MediaPause if a_on else QStyle.SP_MediaPlay
        self._play_action.setIcon(self.style().standardIcon(icon))


# Pseudo-data helpers
def _make_rd_map(n_doppler, n_range, targets_idx):
    rd = np.random.normal(-65, 4, (n_doppler, n_range)).astype(np.float32)
    for di, ri in targets_idx:
        rd[di - 1 : di + 2, ri - 2 : ri + 3] += np.random.uniform(25, 35)
    return rd


def _make_spectrogram(n_time, n_freq):
    spec = np.random.normal(-70, 3, (n_time, n_freq)).astype(np.float32)
    # Add a chirp ridge
    for i in range(n_time):
        fi = int(n_freq * i / n_time)
        spec[i, max(0, fi - 2) : fi + 3] += 30
    return spec


if __name__ == "__main__":
    from .config import RadarConfig

    app = QApplication(sys.argv)
    # TDM so the Sector-PPI and the azimuth column show
    window = RadarDisplay(a_config=RadarConfig(TDM_EN=True))
    window.show()

    n_doppler, n_range = 64, 300
    ranges = np.linspace(0, 3000, n_range)
    velocities = np.linspace(-15, 15, n_doppler)

    target_idx = [(32, 50), (20, 120), (45, 200)]
    rd_up = _make_rd_map(n_doppler, n_range, target_idx)
    rd_down = _make_rd_map(n_doppler, n_range, target_idx)

    targets = [
        {"r": ranges[50], "v": velocities[32], "theta": 0.0, "kind": "both"},
        {"r": ranges[120], "v": velocities[20], "theta": -30.0, "kind": "up"},
        {"r": ranges[200], "v": velocities[45], "theta": 30.0, "kind": "down"},
    ]

    window.update(rd_up, rd_down, ranges, velocities, targets)

    n_time, n_freq = 128, 256
    t_ax = np.linspace(0, 6.4e-3, n_time)
    f_ax = np.linspace(-28e6, 28e6, n_freq)
    window.update_signals(
        _make_spectrogram(n_time, n_freq),
        _make_spectrogram(n_time, n_freq),
        t_ax,
        f_ax,
    )

    app.exec()
