# online/app.py
"""
The main application file. Enjoy!
"""

import sys
import traceback
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from common import config, dsp, gui
from online import capture, processing, sdr


class RadarWorker(QThread):
    # --------------------------------
    # Class Attributes
    # --------------------------------
    # 'object' lets us pass numpy arrays / lists / None through untouched
    results = Signal(object, object, object, object, object)
    error = Signal(str)
    reconfigure_done = Signal(bool, object)

    def __init__(self, a_config: config.RadarConfig):
        super().__init__()
        self.config: config.RadarConfig = a_config
        self.pending_cfg: config.RadarConfig = None
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
                    a_config=self.config, a_ctx=ctx, a_sdr=radio
                )
                results = processing.process_rx_data(
                    a_rx=rx, a_config=self.config, a_ctx=ctx
                )
                self.results.emit(*results)
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

    worker.reconfigure_done.connect(
        lambda ok, cfg: display.on_reconfigure_done(ok, cfg)
    )

    display.show()
    worker.start()
    app.aboutToQuit.connect(worker.stop)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
