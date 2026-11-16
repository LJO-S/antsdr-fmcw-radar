import sys
import time
import numpy as np
import dataclasses
from collections import defaultdict
from pathlib import Path
from PySide6.QtCore import QPointF, QRectF, Qt, QLocale, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QTabWidget,
    QTableWidget,
    QTableView,
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
    QGraphicsItem,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDoubleValidator,
    QKeySequence,
)
import pyqtgraph as pg

import common.dsp as dsp
import common.gui_tracks as gui_tracks


class RadarDisplay(QMainWindow):
    """
    Tab 0 - Tracks (TDM: PPI left, no TDM: (r, v) plot left):
    ┌───────────────────────────────────────────────────────┐
    │ [] MTI [] Detections [] Tentative [] Up/Down          │
    │ [] Record [notes                                    ] │
    ├───────────────────────────┬───────────────────────────┤
    │                           │ (r, v) plot over dimmed   │
    │  Sector-PPI x-y (TDM)     │ up-chirp RD map           │
    │                           ├───────────────────────────┤
    │                           │ Track list (QTableView)   │
    └───────────────────────────┴───────────────────────────┘
    Without TDM the left slot holds the (r, v) plot, the right column the list only.

    Tab 1 - Signal:
    ┌─────────────────────────┬─────────────────────────┐
    │  Up-chirp RD map        │  Down-chirp RD map      │
    ├─────────────────────────┴─────────────────────────┤
    │  RX spectrogram                                   │
    ├───────────────────────────────────────────────────┤
    │  IF spectrogram                                   │
    └───────────────────────────────────────────────────┘

    Tab 2 - History (last N s, detections as dots):
    ┌─────────────────────────────────────────┐
    │  Range vs time (RTI)                    │
    ├─────────────────────────────────────────┤
    │  Velocity vs time                       │
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

    Track layer (common/gui_tracks.py): update_tracks(tracks, t) takes Track.snapshot()
    dicts into a TrackStore (intake only, order against update() does not matter); the
    Tracks tab draws heads, 2 s tails and T<num> labels on the (r, v) plot and PPI.
    Detections are faint dots fading over ~1 s: 4 age levels in one image per plot
    (DotImage), no per-CPI scatter. Up/down-only detections stay crosses (newest CPI).
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
    # Class mask lookup table, built once: 0 none, 1 detection (white, half),
    # 2 / 3 / 4 track neutral / approaching / receding. 256 rows: levels (0, 255)
    _HIST_LUT = np.zeros((256, 4), np.uint8)
    _HIST_LUT[1] = (255, 255, 255, 128)
    for _d, _c in gui_tracks.COLORS.items():
        _HIST_LUT[2 + _d] = (*pg.mkColor(_c).getRgb()[:3], 255)
    del _d, _c  # loop names, not class attributes
    _HIST_SEL_PEN = pg.mkPen(gui_tracks.SEL_COLOR, width=2)

    # One timer draws current tab
    # Interval adapts: DUTY x smoothed draw cost (incl. Qt paint after slot) -> GUI <= 1/DUTY of time
    DRAW_INTERVAL_MS = 33  # min: ~30 draws/s cheap scene
    DRAW_INTERVAL_MAX_MS = 250  # max: never freezes below 4 draws/s
    DRAW_DUTY = 4  # interval = 4 x cost -> <= 25 % duty
    DRAW_COST_ALPHA = 0.3  # EMA: one slow draw does not stall, cheap scene recovers
    # (r, v) plot background: CET-L9 brightness
    RD_BG_DIM = 0.35
    TRACK_COLUMNS = ["ID", "r [m]", "v [m/s]", "az [deg]", "SNR [dB]", "age [s]"]
    TRACK_AZ_COL = 3
    CLICK_PICK_PX = 12  # plot click picks a track head within this

    # Sector-PPI: fan drawn, spoke spacing inside it
    XY_FOV_DEG = 60
    XY_SPOKE_DEG = 30

    def __init__(self, a_config):
        super().__init__()
        self.setWindowTitle("FMCW Radar")

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        self._tabs = tabs

        self._config = a_config
        # Track layer state (intake side): tracks and fading detections
        self._store = gui_tracks.TrackStore(
            a_v_limit=a_config.MAX_VELOCITY,
            a_hist_s=max(self.HISTORY_WINDOWS_S),
            a_bin_s=self.HISTORY_BIN_S,
        )
        self._trail = gui_tracks.DetectionTrail()
        # Intake state: newest frame / spectrograms, drawn by timer when dirty
        self._frame = None  # (up, down, ranges, velocities, targets)
        self._tracks_dirty = False  # Tracks tab: RD background, dots, rings
        self._maps_dirty = False  # Signal tab: up / down RD maps
        self._spec_rx = None  # pending (spec) per plot, None once drawn
        self._spec_if = None
        self._spec_axes = None  # (t, f)
        # Dimmed CET-L9: dark enough that the dots stay readable; scaled once here,
        # item opacity would blend per paint
        lut = pg.colormap.get("CET-L9").getLookupTable(nPts=256, alpha=False)
        self._dim_lut = (lut * self.RD_BG_DIM).astype(np.uint8)
        # --------- Tab 0: Tracks ---------
        self._build_tracks_tab(tabs)

        # --------- Tab 1: Signal ---------
        signals_tab = QWidget()
        self._signal_tab = signals_tab
        tabs.addTab(signals_tab, "Signal")
        signals_layout = QVBoxLayout(signals_tab)

        # Up / down RD maps on top; down map only in triangle mode (set_config)
        maps_row = QHBoxLayout()
        signals_layout.addLayout(maps_row, stretch=3)
        for title, attr in [("Up-chirp", "up"), ("Down-chirp", "down")]:
            plot = pg.PlotWidget(title=title)
            plot.setLabel("left", "Range", units="m")
            plot.setLabel("bottom", "Velocity", units="m/s")
            image = pg.ImageItem()
            image.setColorMap(pg.colormap.get("CET-L9"))
            plot.addItem(image)
            setattr(self, f"rd_{attr}_plot", plot)
            setattr(self, f"rd_{attr}_image", image)
            maps_row.addWidget(plot)

        for title, attr in [("RX", "rx"), ("IF", "if")]:
            plot = pg.PlotWidget(title=f"{title} Instantaneuous Freq")
            plot.setLabel("left", "Frequency", units="Hz")
            plot.setLabel("bottom", "Time", units="s")
            image = pg.ImageItem()
            image.setColorMap(pg.colormap.get("CET-L9"))
            plot.addItem(image)
            setattr(self, f"{attr}_spec_plot", plot)
            setattr(self, f"{attr}_spec_image", image)
            signals_layout.addWidget(plot, stretch=2)

        # --------- Tab 2: History ---------
        self._build_history_tab(tabs)

        # --------- Tab 3: Config ---------
        config_tab = QWidget()
        self._config_tab = config_tab
        tabs.addTab(config_tab, "Config")
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

        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        form_widget = QWidget()
        form_layout = QFormLayout(form_widget)
        config_scroll.setWidget(form_widget)
        config_layout_right_col.addWidget(config_scroll)

        self._cfg_form_widget = form_widget  # set_playback_mode() disables it

        fake_target_box = QGroupBox("Fake Target Simulation")
        self.fake_target_box = fake_target_box
        fake_targets_layout = QVBoxLayout(fake_target_box)
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
        fake_targets_button_layout = QHBoxLayout()
        fake_target_add_button = QPushButton("+")
        fake_target_del_button = QPushButton("-")
        fake_targets_button_layout.addWidget(fake_target_add_button)
        fake_targets_button_layout.addWidget(fake_target_del_button)
        fake_targets_layout.addLayout(fake_targets_button_layout)
        config_layout_right_col.addWidget(fake_target_box)

        fake_target_add_button.clicked.connect(self._fake_target_add_row)
        fake_target_del_button.clicked.connect(self._fake_target_del_row)
        self.fake_targets_table.itemChanged.connect(self._fake_target_edited)

        groups = defaultdict(list)
        for field in dataclasses.fields(a_config):
            if not field.metadata:
                continue
            groups[field.metadata["group"]].append(field)

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

        self.re_cfg_button = QPushButton("RE-CONFIGURE")
        self.re_cfg_button.clicked.connect(self.reconfigure)
        self.re_cfg_button.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white;"
            " font-weight: bold; padding: 6px; }"
            "QPushButton:disabled { background-color: #555555; color: #aaaaaa; }"
        )
        config_layout_right_col.addWidget(self.re_cfg_button)

        config_layout.addWidget(cfg_box, stretch=1)

        self.set_config(a_config)

        self._draw_cost_ms = None  # EMA of a draw incl. paint
        self._draw_timer = QTimer(self)
        self._draw_timer.setInterval(self.DRAW_INTERVAL_MS)
        self._draw_timer.timeout.connect(self._on_tick)
        self._draw_timer.start()

    # ---------------------------------
    # Tracks tab
    # ---------------------------------
    def _build_tracks_tab(self, a_tabs):
        tab = QWidget()
        self._tracks_tab = tab
        a_tabs.addTab(tab, "Tracks")
        layout = QVBoxLayout(tab)

        # One toolbar row. Widgets are made here, parented by the row
        bar = QHBoxLayout()
        layout.addLayout(bar)
        self.mti_en_box = QCheckBox("MTI")
        self.mti_en_box.setChecked(self._config.MTI_EN)
        self.mti_en_box.toggled.connect(self.mti_signal_changed)
        self.dets_box = QCheckBox("Detections")
        self.dets_box.setChecked(True)
        self.dets_box.toggled.connect(self._mark_tracks_dirty)
        self.tentative_box = QCheckBox("Tentative")
        self.tentative_box.setChecked(False)
        self.tentative_box.toggled.connect(self._mark_tracks_dirty)
        self.xs_toggle = QCheckBox("Up/Down")
        self.xs_toggle.setChecked(False)
        self.xs_toggle.toggled.connect(self._mark_tracks_dirty)
        # Record: worker owns session, on_recording() mirrors state back
        self.record_box = QCheckBox("Record")
        self.record_notes = QLineEdit()
        self.record_notes.setPlaceholderText(
            "Session notes (read when a session opens)"
        )
        self.record_box.toggled.connect(self.record_changed)
        # Every edit, so later-opened session (RE-CONFIGURE, target edit) gets current
        # text, not text from Record tick
        self.record_notes.textChanged.connect(self.record_notes_changed)
        for w in (self.mti_en_box, self.dets_box, self.tentative_box, self.xs_toggle):
            bar.addWidget(w)
        bar.addSpacing(16)
        bar.addWidget(self.record_box)
        bar.addWidget(self.record_notes, stretch=1)

        # Left slot (~60 %) and right column. set_config() moves the widgets between
        # them, so the plots are built once
        body = QHBoxLayout()
        layout.addLayout(body, stretch=1)
        left = QWidget()
        right = QWidget()
        self._left_lay = QVBoxLayout(left)
        self._right_lay = QVBoxLayout(right)
        for lay in (self._left_lay, self._right_lay):
            lay.setContentsMargins(0, 0, 0, 0)
        body.addWidget(left, stretch=3)
        body.addWidget(right, stretch=2)

        # (r, v) plot over the dimmed up-chirp RD map
        self.det_plot = pg.PlotWidget(title="Range - velocity")
        self.det_plot.setLabel("left", "Range", units="m")
        self.det_plot.setLabel("bottom", "Velocity", units="m/s")
        self.det_plot.setBackground("#0a1628")
        self.det_plot.enableAutoRange(False)
        self.det_image = pg.ImageItem()
        self.det_image.setLookupTable(self._dim_lut)
        self.det_image.setZValue(-10)  # behind the scatters
        self.det_plot.addItem(self.det_image, ignoreBounds=True)

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
        # Single-ramp detections (triangle, Up/Down on): newest CPI only, crosses
        self.xy_scatter = pg.ScatterPlotItem(
            size=8, symbol="x", pen=pg.mkPen((255, 255, 255, 100), width=2)
        )
        self.xy_plot.addItem(self.xy_scatter, ignoreBounds=True)

        # Cheap grid of the (r, v) plot (showGrid costs ~4 ms per axis per paint):
        # one NaN-separated curve, rebuilt per config. Between RD image and scatters
        self.det_grid = pg.PlotCurveItem(
            pen=pg.mkPen((255, 255, 255, 40), width=1), connect="finite"
        )
        self.det_grid.setZValue(-5)
        # Static grids: blit a cached pixmap instead of re-stroking every frame (the
        # fading dots and tracks change each CPI, which repaints the whole plot)
        for item in (self.xy_grid, self.xy_xlines, self.det_grid):
            item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        self.det_plot.addItem(self.det_grid, ignoreBounds=True)

        # Fading detection dots: one image per plot (see DotImage)
        self.det_dots = gui_tracks.DotImage(self.det_plot)
        self.xy_dots = gui_tracks.DotImage(self.xy_plot)
        self.scatter_up = pg.ScatterPlotItem(
            size=8, symbol="x", pen=pg.mkPen((255, 255, 255, 100), width=2)
        )
        self.scatter_down = pg.ScatterPlotItem(
            size=8, symbol="x", pen=pg.mkPen((0, 255, 255, 100), width=2)
        )
        for item in (self.scatter_up, self.scatter_down):
            self.det_plot.addItem(item, ignoreBounds=True)
        self.det_layer = gui_tracks.TrackLayer(self.det_plot, "rv")
        self.xy_layer = gui_tracks.TrackLayer(self.xy_plot, "xy")
        # Playback only: live-recorded detections, rings around replayed dots.
        # Drawn last (on top), hidden until update_recorded()
        self.scatter_recorded = pg.ScatterPlotItem(
            size=16,
            symbol="o",
            pen=pg.mkPen((255, 0, 255), width=2),
            brush=pg.mkBrush(None),
        )
        self.det_plot.addItem(self.scatter_recorded, ignoreBounds=True)
        self.scatter_recorded.setVisible(False)
        self._recorded = []
        self._show_recorded = False

        # Track list (confirmed tracks); selection = one track id (see _select)
        self._sel_id = None
        self._sel_state = None  # TrackState it was selected on: identity = same track
        self._sync = False  # programmatic row change: ignore selectionChanged
        self._table_pending = False  # refresh skipped by the 5 Hz throttle
        self.track_view = QTableView()
        self.track_model = gui_tracks.TrackTableModel(self.TRACK_COLUMNS, self)
        self.track_view.setModel(self.track_model)
        self.track_view.verticalHeader().setVisible(False)
        self.track_view.setEditTriggers(QTableView.NoEditTriggers)
        self.track_view.setSelectionBehavior(QTableView.SelectRows)
        self.track_view.setSelectionMode(QTableView.SingleSelection)
        self.track_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.track_view.selectionModel().selectionChanged.connect(self._on_row_selected)
        for plot, kind in ((self.det_plot, "rv"), (self.xy_plot, "xy")):
            plot.scene().sigMouseClicked.connect(
                lambda ev, p=plot, k=kind: self._on_plot_click(p, k, ev)
            )

    def _layout_tracks(self, a_tdm):
        """TDM: PPI left, (r, v) plot over the track list right. No TDM: (r, v) plot
        left, list alone right. Same widgets every time (reparented, never rebuilt)."""
        movers = (self.xy_plot, self.det_plot, self.track_view)
        for lay in (self._left_lay, self._right_lay):
            for w in movers:
                lay.removeWidget(w)
        if a_tdm:
            self._left_lay.addWidget(self.xy_plot)
            self._right_lay.addWidget(self.det_plot, stretch=3)
            self._right_lay.addWidget(self.track_view, stretch=2)
        else:
            self._left_lay.addWidget(self.det_plot)
            self._right_lay.addWidget(self.track_view)
        # Reparenting hides a widget: show again, PPI only with TDM
        self.det_plot.setVisible(True)
        self.track_view.setVisible(True)
        self.xy_plot.setVisible(a_tdm)
        self.track_view.setColumnHidden(self.TRACK_AZ_COL, not a_tdm)

    @staticmethod
    def _image_rect(a_velocities, a_ranges, a_mirror=False):
        """Pixel-centred rect of an RD image (axis 0 velocity, axis 1 range): one bin
        per pixel, a cell's (v, r) at its centre. a_mirror: Doppler-reversed (down map),
        negative width."""
        n_v, n_r = len(a_velocities), len(a_ranges)
        dv = float(a_velocities[1] - a_velocities[0]) if n_v > 1 else 1.0
        dr = float(a_ranges[1] - a_ranges[0]) if n_r > 1 else 1.0
        y = float(a_ranges[0]) - dr / 2
        if a_mirror:
            return QRectF(float(a_velocities[-1]) + dv / 2, y, -n_v * dv, n_r * dr)
        return QRectF(float(a_velocities[0]) - dv / 2, y, n_v * dv, n_r * dr)

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
        """Worker's session state: dir, or None if not recording (or disk error
        stopped it). No record_changed re-emit."""
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
        """Session owns config and fake targets: read-only, RE-CONFIGURE hidden, no
        recording, status bar says not live. MTI box only shows what each CPI ran with
        (player bar overrides)."""
        self.setWindowTitle("FMCW Radar - Playback")
        self.record_box.hide()
        self.record_notes.hide()
        self.mti_en_box.setEnabled(False)  # still shown: what each CPI ran with
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
        """Fill fake-target table, no fake_targets_changed emit."""
        t = self.fake_targets_table
        t.blockSignals(True)
        t.setRowCount(0)
        for row, target in enumerate(a_targets):
            t.insertRow(row)
            for col, (key, header, default) in enumerate(self.FAKE_TGT_COLUMNS):
                t.setItem(row, col, QTableWidgetItem(str(target.get(key, default))))
        t.blockSignals(False)

    def show_mti(self, a_on):
        """Mirror MTI a CPI ran with, no mti_signal_changed emit."""
        self.mti_en_box.blockSignals(True)
        self.mti_en_box.setChecked(a_on)
        self.mti_en_box.blockSignals(False)

    def update_recorded(self, a_targets):
        """Detections recorded live for this CPI (None: no recorded line), drawn as
        rings with frame. Same kind filter as update()."""
        self._recorded = a_targets or []
        self._tracks_dirty = True

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

    def _mark_tracks_dirty(self, *_args):
        self._tracks_dirty = True

    def update(
        self, a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets, a_t=None
    ):
        """Intake only: keeps frame (by reference, see class docstring), feeds History
        buffers; timer draws. a_t: CPI time in s (playback passes recorded one);
        None = now (live app)."""
        self._frame = (a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets)
        self._tracks_dirty = True
        self._maps_dirty = True
        t = time.monotonic() if a_t is None else a_t
        self._take_detections(t, a_targets, a_rd_down_db is None)
        self._update_history(t, a_rd_up_db, a_ranges, a_velocities, a_targets)

    def _take_detections(self, a_t, a_targets, a_sawtooth):
        """Paired detections ("both", or any in sawtooth) join the fading trail as
        (v, r) and, with theta, (x, y). Single-ramp ones are drawn for the newest CPI
        only (crosses), straight from the frame."""
        tri = self._config.TRIANGLE_EN and not a_sawtooth
        shown = [d for d in a_targets if not tri or d["kind"] == "both"]
        rv = np.array([(d["v"], d["r"]) for d in shown], dtype=float).reshape(-1, 2)
        xy = np.array(
            [
                (d["r"] * np.sin(th), d["r"] * np.cos(th))
                for d in shown
                if d.get("theta") is not None
                for th in (np.radians(d["theta"]),)
            ],
            dtype=float,
        ).reshape(-1, 2)
        self._trail.add(a_t, rv, xy)

    def update_tracks(self, a_tracks, a_t):
        """Intake only: Track.snapshot() dicts of one CPI, a_t = its capture time.
        [] / None t = cleared (tracker rebuilt or stopped). Order against update()
        does not matter."""
        if not a_tracks or a_t is None:
            self._store.clear()
        else:
            self._store.update(a_tracks, a_t)
            self._update_history_tracks(a_t)
        self._hist_dirty = True  # masks, selected curve (also a track death)
        # Selected track gone, or restarted (store makes a new state on a restart)
        if (
            self._sel_id is not None
            and self._store.tracks.get(self._sel_id) is not self._sel_state
        ):
            self._clear_selection()
        self._tracks_dirty = True

    # ---------------------------------
    # Selection: one track id from row or plot click
    # ---------------------------------
    def selected_id(self):
        return self._sel_id

    def _select(self, a_id):
        if a_id == self._sel_id:
            return
        st = self._store.tracks.get(a_id)
        if st is None or not st.confirmed:
            return
        self._sel_id, self._sel_state = a_id, st
        self._tracks_dirty = self._hist_dirty = True  # redraw, also paused with no new data
        self._refresh_table(a_force=True)  # the row must exist
        self._show_row()

    def _clear_selection(self):
        if self._sel_id is None:
            return
        self._sel_id = self._sel_state = None
        self._tracks_dirty = self._hist_dirty = True
        self._show_row()

    def _show_row(self):
        """Row of the view follows the selected id (no feedback into _on_row_selected)."""
        row = self.track_model.row_of(self._sel_id) if self._sel_id is not None else -1
        self._sync = True
        try:
            if row >= 0:
                self.track_view.selectRow(row)
            else:
                self.track_view.clearSelection()
        finally:
            self._sync = False

    def _refresh_table(self, a_force=False):
        kind = self.track_model.refresh(self._store, a_force)
        self._table_pending = kind is None
        if kind == "reset":
            self._show_row()  # reset drops the view selection: restore by id
        return kind

    def _on_row_selected(self, *_args):
        if self._sync:
            return
        rows = self.track_view.selectionModel().selectedRows()
        if rows:
            self._select(self.track_model.id_at(rows[0].row()))
        else:
            self._clear_selection()

    def _on_plot_click(self, a_plot, a_kind, a_ev):
        """Left click on the data area: nearest head within CLICK_PICK_PX selects, none
        clears. Drags (pan / zoom) never emit sigMouseClicked."""
        if a_ev.button() != Qt.LeftButton:
            return
        vb = a_plot.getPlotItem().vb
        pos = a_ev.scenePos()
        if not vb.sceneBoundingRect().contains(pos):
            return
        best, best_d = None, self.CLICK_PICK_PX
        for st in self._store.confirmed():
            x, y = (st.v, st.r) if a_kind == "rv" else (st.x, st.y)
            if x != x:
                continue
            p = vb.mapViewToScene(QPointF(x, y))
            d = np.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d <= best_d:
                best, best_d = st.id, d
        if best is None:
            self._clear_selection()
        else:
            self._select(best)

    # ---------------------------------
    # Drawing (timer, tab change, draw_now)
    # ---------------------------------
    def _on_tick(self):
        t0 = time.perf_counter()
        if self._draw_current(a_now=False):
            # Qt paints after this slot returns: measure once the posted paint
            # events have run. Nothing drawn costs nothing: no update then
            QTimer.singleShot(0, lambda: self._draw_cost_done(t0))

    def _draw_cost_done(self, a_t0):
        ms = 1e3 * (time.perf_counter() - a_t0)
        a = self.DRAW_COST_ALPHA
        self._draw_cost_ms = (
            ms
            if self._draw_cost_ms is None
            else (a * ms + (1 - a) * self._draw_cost_ms)
        )
        interval = int(
            min(
                max(self.DRAW_DUTY * self._draw_cost_ms, self.DRAW_INTERVAL_MS),
                self.DRAW_INTERVAL_MAX_MS,
            )
        )
        if interval != self._draw_timer.interval():
            self._draw_timer.setInterval(interval)

    def draw_now(self, a_table=False):
        """One full draw of current tab, ignoring timer and throttles. For tests and
        profiler. The track list stays on its 5 Hz throttle unless a_table (a forced
        refresh repaints the list: ~6 ms, not part of a per-CPI draw)."""
        self._tracks_dirty = True
        self._maps_dirty = True
        self._hist_dirty = True
        self._draw_current(a_now=True, a_table=a_table)

    def _draw_current(self, a_now, a_table=None):
        """Draw tab on screen if anything new. a_now ignores 5 Hz throttles (tab
        change, draw_now). Configuration has nothing per frame."""
        tab = self._tabs.currentWidget()
        if a_table is None:
            a_table = a_now  # tab change: the list shows the current state
        if tab is self._tracks_tab:
            return self._draw_tracks(a_table)
        if tab is self._signal_tab:
            return self._draw_signals()
        if tab is self._history_tab:
            return self._draw_history_tab(a_now)
        return False

    def _draw_tracks(self, a_table=False):
        if not self._tracks_dirty:
            if self._table_pending:  # last refresh was throttled: no stale list at rest
                self._refresh_table()
            return False
        self._tracks_dirty = False
        self._refresh_table(a_table)
        if self._frame is not None:
            self._draw_rd_and_dets(*self._frame)
        self._draw_track_layers()
        self._draw_recorded()
        return True

    def _draw_rd_and_dets(
        self, a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets
    ):
        # Dimmed up-chirp map behind the dots (down map: Signal tab only)
        self.det_image.setImage(a_rd_up_db, levels=(-80, 0))
        self.det_image.setRect(self._image_rect(a_velocities, a_ranges))

        # Detections: fading dots (both plots), crosses for the newest CPI
        show_xs = self.xs_toggle.isChecked()
        show_dets = self.dets_box.isChecked()
        tdm = self._config.TDM_EN
        crosses = a_rd_down_db is not None and show_xs
        up = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "up"]
        down = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "down"]
        for item, pts in ((self.scatter_up, up), (self.scatter_down, down)):
            show = show_dets and crosses and len(pts) > 0
            if show:
                item.setData(pos=pts, size=8)
            item.setVisible(show)
        if show_dets:
            rv, lrv, xy, lxy = self._trail.arrays()
            self.det_dots.set_points(rv, lrv)
            if tdm:
                self.xy_dots.set_points(xy, lxy)
        else:
            self.det_dots.item.setVisible(False)
            self.xy_dots.item.setVisible(False)
        if tdm:
            xy_pts = np.array(
                [
                    (
                        t["r"] * np.sin(np.radians(t["theta"])),
                        t["r"] * np.cos(np.radians(t["theta"])),
                    )
                    for t in a_targets
                    if t.get("theta") is not None and t["kind"] != "both"
                ]
            ).reshape(-1, 2)
            show = show_dets and crosses and len(xy_pts) > 0
            if show:
                self.xy_scatter.setData(x=xy_pts[:, 0], y=xy_pts[:, 1], size=8)
            self.xy_scatter.setVisible(show)

    def _draw_track_layers(self):
        tent = self.tentative_box.isChecked()
        self.det_layer.draw(self._store, tent, self._sel_id)
        if self._config.TDM_EN:
            self.xy_layer.draw(self._store, tent, self._sel_id)

    # ---------------------------------
    # Sector-PPI (TDM)
    # ---------------------------------
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

        row = QHBoxLayout()
        row.addWidget(QLabel("Window"))
        self.history_window_box = QComboBox()
        self.history_window_box.addItems([f"{s} s" for s in self.HISTORY_WINDOWS_S])
        self.history_window_box.setCurrentIndex(1)  # 30 s
        self.history_window_box.currentIndexChanged.connect(
            lambda _: self._reset_history()
        )
        row.addWidget(self.history_window_box)
        row.addStretch(1)
        layout.addLayout(row)

        def _plot(a_title, a_label, a_units):
            plot = pg.PlotWidget(title=a_title)
            plot.setLabel("left", a_label, units=a_units)
            plot.setLabel("bottom", "Time to CPI", units="s")
            image = pg.ImageItem()
            image.setColorMap(pg.colormap.get("CET-L9"))
            plot.addItem(image)
            # Class mask on the image grid (see _HIST_LUT)
            dets = pg.ImageItem()
            dets.setLookupTable(self._HIST_LUT)
            plot.addItem(dets)
            # Selected track: one curve, above the masks, never sets the range
            curve = pg.PlotCurveItem(pen=self._HIST_SEL_PEN, connect="finite")
            curve.setZValue(10)
            curve.setVisible(False)
            plot.addItem(curve, ignoreBounds=True)
            return plot, image, dets, curve

        self.rti_plot, self.rti_image, self.rti_dets, self.rti_sel = _plot(
            "Range", "Range", "m"
        )
        self.vti_plot, self.vti_image, self.vti_dets, self.vti_sel = _plot(
            "Velocity", "Velocity", "m/s"
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
        self._hist_rdet = None  # (n_cols, n_range) uint8 class mask, same grid
        self._hist_vdet = None  # (n_cols, n_velocity)
        self._hist_last_draw = 0.0
        self._hist_dirty = False
        self.rti_image.clear()
        self.vti_image.clear()
        self.rti_dets.clear()
        self.vti_dets.clear()
        self.rti_sel.setVisible(False)
        self.vti_sel.setVisible(False)
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
        if self._hist_rti is None:
            n_cols = int(round(self._hist_window_s / self.HISTORY_BIN_S))
            self._hist_rti = np.full(
                (n_cols, len(a_ranges)), self.HISTORY_FLOOR_DB, np.float32
            )
            self._hist_vti = np.full(
                (n_cols, len(a_velocities)), self.HISTORY_FLOOR_DB, np.float32
            )
            self._hist_rdet = np.zeros((n_cols, len(a_ranges)), np.uint8)
            self._hist_vdet = np.zeros((n_cols, len(a_velocities)), np.uint8)
            self._hist_bin = int(a_t // self.HISTORY_BIN_S)
            self._hist_axes = axes
        self._hist_advance(a_t)
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
                # maximum: a track already in the cell (tracks came first) wins
                buf[-1, idx] = np.maximum(buf[-1, idx], 1)

        # Buffers fill every CPI; timer draws them (History tab only, <= 5 Hz)
        self._hist_dirty = True

    def _hist_advance(self, a_t):
        """Move the window to the time bin of a_t (buffers shift left, new columns
        empty). Whichever of update() / update_tracks() comes first for a CPI does it.
        No-op without buffers."""
        if self._hist_rti is None:
            return
        n_cols = self._hist_rti.shape[0]
        new_bin = int(a_t // self.HISTORY_BIN_S)
        shift = min(new_bin - self._hist_bin, n_cols)
        if shift > 0:
            for buf in (self._hist_rti, self._hist_vti):
                buf[:-shift] = buf[shift:].copy()
                buf[-shift:] = self.HISTORY_FLOOR_DB
            for buf in (self._hist_rdet, self._hist_vdet):
                buf[:-shift] = buf[shift:].copy()
                buf[-shift:] = 0
            self._hist_bin = new_bin

    def _update_history_tracks(self, a_t):
        """Confirmed tracks into the newest column of the class masks, one write per
        plot. Skipped without buffers, or when a_t is older than the history (seek:
        the next update() resets it)."""
        if self._hist_rti is None or a_t < self._hist_t:
            return
        self._hist_advance(a_t)
        sts = self._store.confirmed()
        if not sts:
            return
        cls = np.array([2 + st.cls for st in sts], dtype=np.uint8)
        rv = np.array([(st.r, st.v) for st in sts], dtype=float)
        axes = self._hist_axes
        for vals, buf, (n, lo, hi) in (
            (rv[:, 0], self._hist_rdet, axes[0:3]),
            (rv[:, 1], self._hist_vdet, axes[3:6]),
        ):
            step = (hi - lo) / (n - 1) if n > 1 else 1.0
            idx = np.clip(np.rint((vals - lo) / step), 0, n - 1).astype(int)
            np.maximum.at(buf[-1], idx, cls)

    def _on_tab_changed(self, _index):
        # Shown tab never stale: draw what it missed at once
        self._draw_current(a_now=True)

    def _draw_history_tab(self, a_now):
        if not self._hist_dirty or self._hist_t is None:
            return False
        now = time.monotonic()
        if a_now or now - self._hist_last_draw >= self.HISTORY_REDRAW_S:
            self._hist_dirty = False
            self._hist_last_draw = now
            self._draw_history()
            return True
        return False

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
            np.maximum(m[:, 1:], mask[:, :-1], out=m[:, 1:])
            np.maximum(m[:, :-1], mask[:, 1:], out=m[:, :-1])
            item.setImage(m, levels=(0, 255), autoLevels=False)
            item.setRect(
                QRectF(x_left, rect[0], self._hist_window_s, rect[1] - rect[0])
            )
        # Selected track's curve, x like the images (s to the newest CPI)
        st = self._sel_state
        if st is None:
            self.rti_sel.setVisible(False)
            self.vti_sel.setVisible(False)
        else:
            t, r, v = st.hist_window(self._hist_t - self._hist_window_s)
            x = t - self._hist_t
            self.rti_sel.setData(x, r)
            self.vti_sel.setData(x, v)
            self.rti_sel.setVisible(len(x) > 0)
            self.vti_sel.setVisible(len(x) > 0)

    def update_signals(self, a_rx_spec, a_if_spec, a_t, a_f):
        """Intake only; drawn while Signals tab current. None keeps that plot's
        pending or drawn image."""
        if a_rx_spec is not None:
            self._spec_rx = a_rx_spec
        if a_if_spec is not None:
            self._spec_if = a_if_spec
        self._spec_axes = (a_t, a_f)

    def _draw_signals(self):
        drew = False
        if self._maps_dirty and self._frame is not None:
            drew = True
            self._maps_dirty = False
            up, down, ranges, vels, _ = self._frame
            self.rd_up_image.setImage(up, levels=(-80, 0))
            self.rd_up_image.setRect(self._image_rect(vels, ranges))
            if down is not None:
                self.rd_down_image.setImage(down, levels=(-80, 0))
                # Doppler-reversed, as the down chirp sees it
                self.rd_down_image.setRect(self._image_rect(vels, ranges, True))
        if self._spec_rx is None and self._spec_if is None:
            return drew
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
        return True

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
        self._config = a_config
        # Pending data shaped by old config (axes, map size) dropped, recorded rings
        # too (next draw clears them)
        self._frame = None
        self._maps_dirty = False
        self._spec_rx = self._spec_if = self._spec_axes = None
        self._recorded = []
        self._tracks_dirty = True
        # Tracks, fading dots, layers: shaped by the old config
        self._store.clear()
        self._store.set_v_limit(a_config.MAX_VELOCITY)
        self._sel_id = self._sel_state = None
        self._refresh_table(a_force=True)  # empties the list (and its selection)
        self._trail.clear()
        self.det_layer.clear()
        self.xy_layer.clear()
        # Sawtooth never writes down map: no stale triangle one
        if not a_config.TRIANGLE_EN:
            self.rd_down_image.clear()
        self.rd_down_plot.setVisible(a_config.TRIANGLE_EN)
        # New config (or playback session) starts new history
        self._reset_history()

        # Angle only with TDM: Sector-PPI, azimuth column, slot of the (r, v) plot
        self._layout_tracks(a_config.TDM_EN)
        self.xy_scatter.setVisible(False)
        self._draw_xy_grid(a_config.MAX_RANGE)

        # Fabric IF mode: captured array is IF so hide IF spectrogram
        self.if_spec_plot.setVisible(not a_config.FABRIC_DECHIRP_EN)
        self.rx_spec_plot.setTitle(
            "RX (fabric IF) Instantaneuous Freq"
            if a_config.FABRIC_DECHIRP_EN
            else "RX Instantaneuous Freq"
        )

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

        for name, f in self._cfg_fields.items():
            v = getattr(a_config, name)
            self.write_cfg_reg(a_field=f, a_value=v)

        # 2 chirp Tx curve
        tx_seq = np.tile(dsp.generate_chirp(a_config=a_config), 2)
        f = dsp.inst_freq(a_signal=tx_seq, a_radar_config=a_config)
        t = np.arange(len(tx_seq)) / a_config.FS
        self.tx_curve.setData(t[: len(f)], f)

        # Cheap grid at round steps: fixed per config
        v_max, r_max = a_config.MAX_VELOCITY, a_config.MAX_RANGE
        gx, gy = gui_tracks.grid_lines(
            gui_tracks.nice_ticks(-v_max, v_max),
            gui_tracks.nice_ticks(0.0, r_max),
            (-v_max, v_max),
            (0.0, r_max),
        )
        self.det_grid.setData(x=gx, y=gy)
        # Fading-dot grids: ~5 px cells ((r, v) plot) / rmax / 140 (PPI)
        self.det_dots.configure(-v_max, v_max, 0.0, r_max, 128, 100)
        half_w = r_max * np.sin(np.radians(self.XY_FOV_DEG))
        self.xy_dots.configure(
            -half_w, half_w, 0.0, r_max, int(round(2 * half_w / r_max * 140)), 140
        )

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
        # Drag: seek on release (each seek replays a CPI). Groove click / key: at once
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
        # Fixed widths from session size: no jitter
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
        # New session: MTI as recorded
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

    # Fake moving tracks: (num, status, r0, v, theta0, coasting). Confirmed
    # approaching / receding / slow, one coasting, one tentative
    fakes = [
        (0, "confirmed", 1800.0, -9.0, -20.0, False),
        (1, "confirmed", 600.0, 7.0, 15.0, False),
        (2, "confirmed", 1200.0, 0.1, 35.0, False),
        (3, "confirmed", 2200.0, -4.0, 0.0, True),
        (None, "tentative", 900.0, 3.0, -35.0, False),
    ]
    clock = {"t": 0.0}

    def tick():
        clock["t"] += 0.05
        t = clock["t"]
        snaps = []
        for i, (num, status, r0, v, th0, coast) in enumerate(fakes):
            r = 300.0 + (r0 - 300.0 + v * t) % 2500.0
            snaps.append(
                {
                    "id": i,
                    "num": num,
                    "status": status,
                    "r": r,
                    "v": v,
                    "theta": th0 + 5 * np.sin(0.5 * t + i),
                    "birth_t": 0.0,
                    "snr_db": 20.0,
                    "age": int(t / 0.05),
                    "nis": None,
                    "kin_ok": True,
                    "coasting": coast,
                }
            )
        window.update(
            rd_up,
            rd_down,
            ranges,
            velocities,
            [
                {"r": s["r"], "v": s["v"], "theta": s["theta"], "kind": "both"}
                for s in snaps[:3]
            ],
            a_t=t,
        )
        window.update_tracks(snaps, t)

    demo_timer = QTimer(window)
    demo_timer.timeout.connect(tick)
    demo_timer.start(50)

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
