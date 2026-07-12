import sys
import numpy as np
import dataclasses
from collections import defaultdict
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
)
from PySide6.QtGui import QBrush, QColor, QDoubleValidator
import pyqtgraph as pg

import common.dsp as dsp


class RadarDisplay(QMainWindow):
    """
    Tab 0 - Radar:
    ┌─────────────────────────┬─────────────────────────┐
    │  Up-chirp RD map        │            │            │
    ├─────────────────────────┤ Detections │ Detections │
    │  Down-chirp RD map      │    Plot    │    List    │
    └─────────────────────────┴─────────────────────────┘

    Tab 1 - Signals:
    ┌─────────────────────────────────────────┐
    │  RX spectrogram                         │
    ├─────────────────────────────────────────┤
    │  IF spectrogram                         │
    └─────────────────────────────────────────┘

    Tab 2 - Config:
    ┌─────────────────────────┬─────────────┐
    │  Tx Spectrogram         │ Cfg Params  │
    ├─────────────────────────├─────────────┤
    │  Radar Parameters       │ Cfg Button  │
    └─────────────────────────┴─────────────┘
    """

    reconfigure_requested = Signal(object)

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
        # B. Middle column = detections scatter + toggle
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

        # --------- Tab 1: Signals ---------
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

        # --------- Tab 2: Config ---------
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

    def update(self, a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets):
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
                    table_item.setBackground(QBrush(QColor("#5c2a2a")))
                    table_item.setForeground(QBrush(QColor("white")))
                # elif t["v"] < -2 * vel_res and col == 2:
                elif t["v"] < -5 and col == 2:
                    table_item.setBackground(QBrush(QColor("#25415c")))
                self.det_table.setItem(i, col, table_item)

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
            image.setImage(spec, levels=(-80, 0))
            image.setRect(rect)

    def set_config(self, a_config):
        # Store new config
        self._config = a_config

        # Update radar params
        radar_params = a_config.derived_params()
        self.param_widget.setRowCount(len(radar_params))  # Flush rows
        for i, (name, (value, unit)) in enumerate(radar_params.items()):
            for col, item in enumerate((name, f"{value:.1f}", unit)):
                table_item = QTableWidgetItem(item)
                table_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table_item.setForeground(QBrush(QColor("white")))
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
