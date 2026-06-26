import sys
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QTabWidget,
    QHBoxLayout,
    QVBoxLayout,
)
import pyqtgraph as pg


class RadarDisplay(QMainWindow):
    """
    Tab 0 — Radar:
    ┌─────────────────────────┬───────────────┐
    │  Up-chirp RD map        │               │
    ├─────────────────────────┤  Detections   │
    │  Down-chirp RD map      │               │
    └─────────────────────────┴───────────────┘

    Tab 1 — Signals:
    ┌─────────────────────────────────────────┐
    │  TX spectrogram                         │
    ├─────────────────────────────────────────┤
    │  RX spectrogram                         │
    ├─────────────────────────────────────────┤
    │  IF spectrogram                         │
    └─────────────────────────────────────────┘
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FMCW Radar")

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # --------- Tab 0: Radar ---------
        radar_tab = QWidget()
        tabs.addTab(radar_tab, "Radar")
        radar_layout = QHBoxLayout(radar_tab)

        # A. Left column = up/down RD maps
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

        # B. Right column = detections scatter
        self.det_plot = pg.PlotWidget(title="Detections")
        self.det_plot.setLabel("left", "Range", units="m")
        self.det_plot.setLabel("bottom", "Velocity", units="m/s")
        self.det_plot.setBackground("#0a1628")
        self.det_plot.showGrid(x=True, y=True, alpha=0.3)
        radar_layout.addWidget(self.det_plot, stretch=1)

        self.scatter_both = pg.ScatterPlotItem(
            size=10, symbol="o", pen=pg.mkPen(None), brush=pg.mkBrush("yellow")
        )
        self.scatter_up = pg.ScatterPlotItem(
            size=8, symbol="x", pen=pg.mkPen("white", width=2)
        )
        self.scatter_down = pg.ScatterPlotItem(
            size=8, symbol="x", pen=pg.mkPen("cyan", width=2)
        )
        self.det_plot.addItem(self.scatter_both)
        self.det_plot.addItem(self.scatter_up)
        self.det_plot.addItem(self.scatter_down)

        # --------- Tab 1: Signals ---------
        signals_tab = QWidget()
        tabs.addTab(signals_tab, "Signals")
        signals_layout = QVBoxLayout(signals_tab)

        for title, attr in [("TX", "tx"), ("RX", "rx"), ("IF", "if")]:
            plot = pg.PlotWidget(title=f"{title} Spectrogram")
            plot.setLabel("left", "Frequency", units="Hz")
            plot.setLabel("bottom", "Time", units="s")
            image = pg.ImageItem()
            image.setColorMap(pg.colormap.get("CET-L9"))
            plot.addItem(image)
            setattr(self, f"{attr}_spec_plot", plot)
            setattr(self, f"{attr}_spec_image", image)
            signals_layout.addWidget(plot)

    def update(self, a_rd_up_db, a_rd_down_db, a_ranges, a_velocities, a_targets):
        rect = QRectF(
            float(a_velocities[0]),
            float(a_ranges[0]),
            float(a_velocities[-1] - a_velocities[0]),
            float(a_ranges[-1] - a_ranges[0]),
        )

        self.rd_up_image.setImage(a_rd_up_db, levels=(-80, 0))
        self.rd_up_image.setRect(rect)

        if a_rd_down_db is not None:
            self.rd_down_image.setImage(a_rd_down_db, levels=(-80, 0))
            self.rd_down_image.setRect(rect)

        # Split detections by kind
        both = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "both"]
        up = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "up"]
        down = [(t["v"], t["r"]) for t in a_targets if t["kind"] == "down"]

        self.scatter_both.setData(pos=both or [(0, 0)], size=10)
        self.scatter_up.setData(pos=up or [(0, 0)], size=8)
        self.scatter_down.setData(pos=down or [(0, 0)], size=8)

        # Hide dummy point when list is empty
        self.scatter_both.setVisible(len(both) > 0)
        self.scatter_up.setVisible(len(up) > 0)
        self.scatter_down.setVisible(len(down) > 0)

    def update_signals(self, a_tx_spec, a_rx_spec, a_if_spec, a_t, a_f):
        rect = QRectF(
            float(a_t[0]),
            float(a_f[0]),
            float(a_t[-1] - a_t[0]),
            float(a_f[-1] - a_f[0]),
        )
        for image, spec in [
            (self.tx_spec_image, a_tx_spec),
            (self.rx_spec_image, a_rx_spec),
            (self.if_spec_image, a_if_spec),
        ]:
            image.setImage(spec)
            image.setRect(rect)


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
    app = QApplication(sys.argv)
    window = RadarDisplay()
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
        _make_spectrogram(n_time, n_freq),
        t_ax,
        f_ax,
    )

    app.exec()
