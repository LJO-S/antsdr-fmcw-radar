import sys
import time
import numpy as np
import dataclasses
from collections import defaultdict, deque
from pathlib import Path
from PySide6.QtCore import QRectF, Qt, QLocale, Signal
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
    │  Up-chirp RD map        │            │            │
    ├─────────────────────────┤ Detections │ Detections │
    │  Down-chirp RD map      │    Plot    │    List    │
    │                         ├─────────────────────────┤
    │                         │ [] MTI                  │
    │                         │ [] Record [notes      ] │
    │                         │ [] Up-down detections   │
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
        ("duration", "Duration [s]", 10.0),
        ("amp", "Amp", 0.1),
    ]

    # History tab: image columns are fixed time bins (the strongest CPI in each wins),
    # so a varying CPI rate cannot stretch the time axis. Images redraw at most 5 Hz;
    # the data is taken every CPI.
    HISTORY_WINDOWS_S = [10, 30, 60, 120]
    HISTORY_BIN_S = 0.1
    HISTORY_REDRAW_S = 0.2
    HISTORY_FLOOR_DB = -80.0  # the RD maps' display floor

    def __init__(self, a_config):
        super().__init__()
        self.setWindowTitle("FMCW Radar")

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self._config = a_config
        # --------- Tab 0: Radar ---------
        radar_tab = QWidget()
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

        self.xs_toggle = QCheckBox("Up/Down Detections")
        self.xs_toggle.setChecked(False)
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
        # Playback only: the detections recorded live, as rings around the replayed
        # dots. Drawn last so they sit on top; hidden until update_recorded()
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

        # Record: the worker owns the session so on_recording() mirrors its state back
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
        # Every edit, so a session opened later (RE-CONFIGURE, target edit) gets the
        # current text, not the text from when Record was ticked
        self.record_notes.textChanged.connect(self.record_notes_changed)

        # ---------------------------------
        # C. Right column = detections list
        # ---------------------------------
        self.det_table = QTableWidget(0, 4)
        self.det_table.setHorizontalHeaderLabels(
            ["#", "Range [m]", "Velocity [m/s]", "RCS [m²]"]
        )
        self.det_table.verticalHeader().setVisible(False)
        self.det_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.det_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        radar_layout.addWidget(self.det_table, stretch=1)

        # --------- Tab 1: History ---------
        self._build_history_tab(tabs)

        # --------- Tab 2: Signals ---------
        signals_tab = QWidget()
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
        self.fake_targets_table = QTableWidget(0, 5)
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
        """Worker's session state: its directory, or None when not recording (or a
        disk error stopped it). Does not re-emit record_changed."""
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
        """The session owns the config and the fake targets, so both turn read-only
        and RE-CONFIGURE goes; no recording; a status bar says this is not live.
        The MTI box only shows what each CPI ran with (the player bar overrides)."""
        self.setWindowTitle("FMCW Radar - Playback")
        self.record_box.hide()
        self.record_notes.hide()
        self.mti_en_box.setEnabled(False)
        # The form, not its scroll area, so the long form still scrolls
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
        """The detections recorded live for this CPI (None: no recorded line), drawn
        as rings. Same kind filter as update()."""
        self._recorded = a_targets or []
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

    def update(
        self, a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets, a_t=None
    ):
        """a_t: the CPI's time in seconds (playback passes the recorded one); None =
        now, which is what the live app uses."""
        # -------------------------
        # Update RD Maps
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
        # Toggle UP/DOWN detections plot
        # -------------------------
        show_xs = self.xs_toggle.isChecked()

        # -------------------------
        # Update detections
        # -------------------------
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

        # -------------------------
        # Update target list
        # -------------------------
        # vel_res = float(a_velocities[1] - a_velocities[0])
        self.det_table.setRowCount(len(a_targets))  # Flush rows
        for i, t in enumerate(a_targets):
            for col, item in enumerate((str(i), f'{t["r"]:.1f}', f'{t["v"]:.2f}', "-")):
                table_item = QTableWidgetItem(item)
                table_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table_item.setForeground(QBrush(QColor("white")))
                # if t["v"] > 2 * vel_res and col == 2:
                if t["v"] > 5 and col == 2:
                    table_item.setBackground(QBrush(QColor("#25415c")))
                    table_item.setForeground(QBrush(QColor("white")))
                # elif t["v"] < -2 * vel_res and col == 2:
                elif t["v"] < -5 and col == 2:
                    table_item.setBackground(QBrush(QColor("#5c2a2a")))
                self.det_table.setItem(i, col, table_item)

        self._update_history(
            time.monotonic() if a_t is None else a_t,
            a_rd_up_db,
            a_ranges,
            a_velocities,
            a_targets,
        )

    # ---------------------------------
    # History tab
    # ---------------------------------
    def _build_history_tab(self, a_tabs):
        tab = QWidget()
        a_tabs.addTab(tab, "History")
        layout = QVBoxLayout(tab)

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
            dets = pg.ScatterPlotItem(
                size=5, pen=pg.mkPen(None), brush=pg.mkBrush(255, 255, 0, 220)
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

    def _reset_history(self):
        """Empty the history. The next CPI sizes the buffers (the bin counts change
        with the config)."""
        self._hist_window_s = self.HISTORY_WINDOWS_S[
            self.history_window_box.currentIndex()
        ]
        self._hist_rti = None  # (n_cols, n_range) dB, oldest column first
        self._hist_vti = None  # (n_cols, n_velocity)
        self._hist_bin = None  # time bin of the newest column
        self._hist_t = None  # time of the newest CPI
        self._hist_axes = None  # the range/velocity axes the buffers were built for
        self._hist_dets = deque()  # (t, r, v)
        self._hist_last_draw = 0.0
        self.rti_image.clear()
        self.vti_image.clear()
        self.rti_dets.setData(pos=[])
        self.vti_dets.setData(pos=[])
        # The window is the time axis; auto-range would not follow the image anyway
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
        # Time going backwards = a playback seek; new axes = a new config
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
            self._hist_bin = new_bin
            self._hist_axes = axes
        shift = min(new_bin - self._hist_bin, n_cols)
        if shift > 0:
            for buf in (self._hist_rti, self._hist_vti):
                buf[:-shift] = buf[shift:].copy()
                buf[-shift:] = self.HISTORY_FLOOR_DB
            self._hist_bin = new_bin
        # RD maps are (velocity, range): strongest cell per range bin and per velocity
        # bin, max-held within the newest time bin
        np.maximum(self._hist_rti[-1], a_rd_db.max(axis=0), out=self._hist_rti[-1])
        np.maximum(self._hist_vti[-1], a_rd_db.max(axis=1), out=self._hist_vti[-1])
        self._hist_t = a_t

        for target in a_targets:
            self._hist_dets.append((a_t, target["r"], target["v"]))
        while self._hist_dets and self._hist_dets[0][0] < a_t - self._hist_window_s:
            self._hist_dets.popleft()

        now = time.monotonic()
        if now - self._hist_last_draw >= self.HISTORY_REDRAW_S:
            self._draw_history()
            self._hist_last_draw = now

    def _draw_history(self):
        # x = seconds relative to the newest CPI; the newest column ends where its
        # time bin ends, just right of 0
        x_right = (self._hist_bin + 1) * self.HISTORY_BIN_S - self._hist_t
        x_left = x_right - self._hist_window_s
        n_r, r0, r1, n_v, v0, v1 = self._hist_axes
        self.rti_image.setImage(self._hist_rti, levels=(self.HISTORY_FLOOR_DB, 0))
        self.rti_image.setRect(QRectF(x_left, r0, self._hist_window_s, r1 - r0))
        self.vti_image.setImage(self._hist_vti, levels=(self.HISTORY_FLOOR_DB, 0))
        self.vti_image.setRect(QRectF(x_left, v0, self._hist_window_s, v1 - v0))
        dets = np.array(self._hist_dets, dtype=float).reshape(-1, 3)
        dt = dets[:, 0] - self._hist_t
        self.rti_dets.setData(x=dt, y=dets[:, 1])
        self.vti_dets.setData(x=dt, y=dets[:, 2])

    def update_signals(self, a_rx_spec, a_if_spec, a_t, a_f):
        rect = QRectF(
            float(a_t[0]),
            float(a_f[0]),
            float(a_t[-1] - a_t[0]),
            float(a_f[-1] - a_f[0]),
        )
        for image, spec in [
            (self.rx_spec_image, a_rx_spec),
            (self.if_spec_image, a_if_spec),
        ]:
            if spec is None:
                continue
            image.setImage(spec, levels=(-80, 0))
            image.setRect(rect)

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
        # Sawtooth never writes the down map: do not leave a stale triangle one up
        if not a_config.TRIANGLE_EN:
            self.rd_down_image.clear()
        # A new config (or a new playback session) starts a new history
        self._reset_history()

        # Fabric IF mode: captured array is IF so hide IF spectrogram
        self.if_spec_plot.setVisible(not a_config.FABRIC_DECHIRP_EN)
        self.rx_spec_plot.setTitle(
            "RX (fabric IF) Instantaneuous Freq"
            if a_config.FABRIC_DECHIRP_EN
            else "RX Instantaneuous Freq"
        )

        # Update radar params
        radar_params = a_config.derived_params()
        # Flag when the chirp, not MAX_RANGE_M, caps the range axis.
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
            v_min=-a_config.MAX_VELOCITY / 2,
            v_max=a_config.MAX_VELOCITY / 2,
        )


class PlayerBar(QToolBar):
    """
    Playback transport (offline.playback), across the top of RadarDisplay:

    [Open] session "notes" | |< < > > | ====o======== block/time | speed | MTI | rings

    Emits requests only. The worker owns the position and reports it back through
    on_loaded / on_position / on_playing, so the bar never runs ahead of the data.
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
        # Seek on release while dragging (each seek replays a CPI), at once on a
        # click in the groove or a key
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
        # Fixed widths from the session's size, so the label does not jitter
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
        # A new session replays with the MTI it was recorded with
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
    window = RadarDisplay(a_config=RadarConfig())
    window.show()

    n_doppler, n_range = 64, 300
    ranges = np.linspace(0, 3000, n_range)
    velocities = np.linspace(-15, 15, n_doppler)

    target_idx = [(32, 50), (20, 120), (45, 200)]
    rd_up = _make_rd_map(n_doppler, n_range, target_idx)
    rd_down = _make_rd_map(n_doppler, n_range, target_idx)

    targets = [
        {"r": ranges[50], "v": velocities[32], "kind": "both"},
        {"r": ranges[120], "v": velocities[20], "kind": "up"},
        {"r": ranges[200], "v": velocities[45], "kind": "down"},
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
