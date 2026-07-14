# online/app.py
"""
The main application file. Enjoy!
"""

import sys
import traceback
import time
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from common import config, dsp, gui
from online import capture, processing, sdr, target_sim


class RadarWorker(QThread):
    # --------------------------------
    # Class Attributes
    # --------------------------------
    # 'object' lets us pass numpy arrays / lists / None through untouched
    results = Signal(object, object, object, object, object)
    error = Signal(str)
    reconfigure_done = Signal(bool, object)
    signals = Signal(object, object, object, object)  # rx, if, t, f

    def __init__(self, a_config: config.RadarConfig):
        super().__init__()
        self.config: config.RadarConfig = a_config
        self.pending_cfg: config.RadarConfig = None
        self.target_sim = target_sim.TargetSim()
        self._last_spec_update: float = 0.0
        self._running: bool = True

    def run(self):
        radio = None
        try:
            ctx = dsp.build_cpi_context(a_config=self.config)
            radio = sdr.AntSDR(a_radar_config=self.config)
            radio.set_loopback(self.config.SDR_LOOPBACK_EN)
            radio.start()
            while self._running:
                if self.pending_cfg is not None:
                    new_cfg, self.pending_cfg = self.pending_cfg, None
                    old_cfg = self.config
                    radio.close()
                    try:
                        self.config = new_cfg
                        ctx = dsp.build_cpi_context(a_config=new_cfg)
                        radio.config = new_cfg
                        radio.set_loopback(new_cfg.SDR_LOOPBACK_EN)
                        radio.start()
                        self.reconfigure_done.emit(True, new_cfg)
                    except Exception:
                        self.error.emit(traceback.format_exc())
                        self.config = old_cfg
                        ctx = dsp.build_cpi_context(a_config=old_cfg)
                        radio.config = old_cfg
                        radio.close()
                        radio.set_loopback(old_cfg.SDR_LOOPBACK_EN)
                        radio.start()
                        self.reconfigure_done.emit(False, old_cfg)
                rx = capture.capture_rx_data(
                    a_config=self.config,
                    a_ctx=ctx,
                    a_sdr=radio,
                    a_target_sim=self.target_sim,
                )
                (
                    rd_map_db_up,
                    rd_map_db_down,
                    detections,
                    ranges,
                    velocities,
                    if_signal,
                ) = processing.process_rx_data(a_rx=rx, a_config=self.config, a_ctx=ctx)
                self.results.emit(
                    rd_map_db_up, rd_map_db_down, detections, ranges, velocities
                )
                if time.monotonic() - self._last_spec_update > 0.5:
                    n2 = 2 * len(ctx.tx_chirp)  # 2 chirps
                    rx_spec, t, f = dsp.spectrogram(
                        a_signal=rx[:n2], a_config=self.config
                    )
                    if_spec, _, _ = dsp.spectrogram(
                        a_signal=if_signal[:n2], a_config=self.config
                    )
                    self.signals.emit(rx_spec, if_spec, t, f)
                    self._last_spec_update = time.monotonic()
        except Exception as e:
            # never let an exception kill the thread silently
            self.error.emit(traceback.format_exc())
        finally:
            if radio is not None:
                print("Stopping SDR...")
                radio.set_loopback(False)
                radio.close()
                print("Successfully stopped SDR!")

    def request_reconfigure(self, a_config):
        self.pending_cfg = a_config

    def set_fake_targets(self, a_fake_targets: list):
        self.target_sim.set_targets(a_targets=a_fake_targets)

    def stop(self):
        print("Stopping app...")
        self._running = False
        self.wait(5000)  # wait for refill thread to finish
        print("Successfully stopped application!")


def main():
    app = QApplication(sys.argv)
    cfg = config.RadarConfig()
    display = gui.RadarDisplay(a_config=cfg)

    worker = RadarWorker(a_config=cfg)
    worker.config.describe()
    worker.results.connect(
        lambda rd_map_db_up, rd_map_db_down, detections, ranges, velocities: display.update(
            rd_map_db_up, rd_map_db_down, ranges, velocities, detections
        )  # slot runs on the GUI thread and Qt queues it because the emit came from the worker
    )
    worker.error.connect(
        print
    )  # slot runs on the GUI thread and Qt queues it because the emit came from the worker

    display.reconfigure_requested.connect(
        worker.request_reconfigure
    )  # slot runs on the GUI thread and run() stalls a bit

    display.fake_targets_changed.connect(worker.set_fake_targets)

    worker.reconfigure_done.connect(
        lambda ok, cfg: display.on_reconfigure_done(ok, cfg)
    )

    worker.signals.connect(
        lambda rx_spec, if_spec, t, f: display.update_signals(rx_spec, if_spec, t, f)
    )

    display.show()
    worker.start()
    app.aboutToQuit.connect(worker.stop)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
