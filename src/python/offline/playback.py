"""
K2 visual playback: a recorded session in the radar GUI, no board needed.

    python -m offline.playback [session_dir]

The live app's RadarDisplay in playback mode (config and fake targets read-only,
no recording, a PLAYBACK status bar), with a gui.PlayerBar on top. PlaybackWorker
replaces RadarWorker: it owns a replay.Player and emits the same results / signals,
plus the recorded detections (drawn as rings) and the play position.
"""

import argparse
import dataclasses
import queue
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication

from common import config, gui
from offline import replay
from online import processing, recorder


class PlaybackWorker(QThread):
    # --------------------------------
    # Class Attributes
    # --------------------------------
    results = Signal(object, object, object, object, object)  # as RadarWorker
    signals = Signal(object, object, object, object)  # rx, if, t, f
    recorded = Signal(object)  # the CPI's recorded targets, or None
    error = Signal(str)
    loaded = Signal(object)  # session dict, see _session_info()
    position = Signal(object)  # dict: block, t, mti, mti_forced, n_replayed, ...
    playing = Signal(bool)

    def __init__(self):
        super().__init__()
        # GUI -> worker. Commands go through a queue so no seek or step is lost or
        # reordered; the speed is a plain float (atomic under CPython)
        self._cmds = queue.SimpleQueue()
        self._speed: float = 1.0
        self._running: bool = True

    # Slots, called on the GUI thread
    def open(self, a_dir):
        self._cmds.put(("open", Path(a_dir)))

    def set_playing(self, a_on: bool):
        self._cmds.put(("play", a_on))

    def step(self, a_delta: int):
        self._cmds.put(("step", a_delta))

    def seek(self, a_block: int):
        self._cmds.put(("seek", a_block))

    def set_mti_mode(self, a_mode):
        self._cmds.put(("mti", a_mode))

    def set_speed(self, a_speed: float):
        self._speed = a_speed

    def stop(self):
        self._running = False
        self.wait(5000)

    def run(self):
        player = None
        playing = False
        next_due = 0.0  # monotonic time the next block is due while playing
        last_spec = 0.0

        def set_playing(a_on):
            nonlocal playing, next_due
            if a_on != playing:
                playing = a_on
                self.playing.emit(a_on)
            next_due = time.monotonic()

        while self._running:
            target = None  # the block to render this pass, if any
            while True:
                try:
                    cmd, arg = self._cmds.get_nowait()
                except queue.Empty:
                    break
                if cmd == "open":
                    set_playing(False)
                    try:
                        new = replay.Player(arg)
                        if new.n_blocks == 0:
                            raise ValueError("no complete block in the session")
                    except Exception:
                        # Keep the session already open, if any
                        self.error.emit(f"Cannot open {arg}:\n{traceback.format_exc()}")
                        continue
                    player = new
                    self.loaded.emit(_session_info(player))
                    target = 0
                elif player is None:
                    continue
                elif cmd == "play":
                    if arg and player.pos >= player.n_blocks - 1:
                        target = 0  # play at the end starts over
                    set_playing(arg)
                elif cmd == "step":
                    set_playing(False)
                    target = (player.pos if target is None else target) + arg
                elif cmd == "seek":
                    target = arg
                elif cmd == "mti":
                    player.mti_override = arg
                    if target is None:
                        target = player.pos  # the same CPI again, with the new MTI

            if player is not None and target is None and playing:
                if player.pos >= player.n_blocks - 1:
                    set_playing(False)
                elif time.monotonic() >= next_due:
                    target = player.pos + 1
            if target is None:
                self.msleep(5)
                continue

            target = min(max(target, 0), player.n_blocks - 1)
            started = time.monotonic()
            try:
                frame = player.render(target)
            except Exception:
                self.error.emit(traceback.format_exc())
                set_playing(False)
                continue
            up, down, targets, ranges, velocities, if_signal = frame.outputs
            recorded = frame.recorded["targets"] if frame.recorded else None
            self.results.emit(up, down, targets, ranges, velocities)
            self.recorded.emit(recorded)
            self.position.emit(
                {
                    "block": frame.block,
                    "t": frame.t - player.index[0][0],
                    "mti": frame.mti,
                    "mti_forced": player.mti_override is not None,
                    "n_replayed": len(targets),
                    "n_recorded": None if recorded is None else len(recorded),
                    "match": frame.match,
                }
            )
            # Every frame while paused (a stepped CPI shows its own spectrogram),
            # ~2 Hz while playing, like the live app
            if not playing or started - last_spec > 0.5:
                self.signals.emit(
                    *processing.spectrograms(
                        a_rx=frame.rx,
                        a_if_signal=if_signal,
                        a_config=player.cfg,
                        a_ctx=player.ctx,
                    )
                )
                last_spec = started

            # Pace on the recorded CPI spacing. A block that takes longer to
            # process than that just plays late: never skip one to catch up.
            if self._speed > 0 and target + 1 < player.n_blocks:
                dt = player.index[target + 1][0] - player.index[target][0]
                next_due = started + dt / self._speed
            else:
                next_due = started


# ===================================================================================
def _session_info(a_player: replay.Player) -> dict:
    session = a_player.session
    # A copy: the player writes MTI_EN into its own config every CPI
    cfg = dataclasses.replace(a_player.cfg)
    if not cfg.FABRIC_DECHIRP_EN:
        mode = "software"
    else:
        mode = "fabric /8" if cfg.FABRIC_DECIM_EN else "fabric /1"
    noisy = cfg.SDR_LOOPBACK_EN and cfg.SDR_LOOPBACK_NOISE_SNR_DB > 0
    link = "loopback" if cfg.SDR_LOOPBACK_EN else "real RF"
    if noisy:
        link += f", loopback noise {cfg.SDR_LOOPBACK_NOISE_SNR_DB:g} dB SNR"
    summary = "\n".join(
        [
            str(a_player.dir),
            f"notes: {session.get('notes', '')}",
            f"started {session.get('start', '?')}, git {session.get('git', '?')}",
            f"{mode}, {'triangle' if cfg.TRIANGLE_EN else 'sawtooth'}, "
            f"{cfg.CHIRP_REPS} reps, {cfg.CHIRP_BW_HZ / 1e6:g} MHz / "
            f"{cfg.CHIRP_DUR_S * 1e6:g} us",
            link,
            f"{len(session['fake_targets'])} fake targets, "
            f"{a_player.n_blocks} CPIs over {a_player.duration:.1f} s",
        ]
    )
    return {
        "dir": a_player.dir,
        "cfg": cfg,
        "notes": session.get("notes", ""),
        "fake_targets": session["fake_targets"],
        "n_blocks": a_player.n_blocks,
        "duration": a_player.duration,
        "noisy": noisy,
        "summary": summary,
    }


def _status(a_pos: dict, a_noisy: bool) -> tuple[str, str | None]:
    """Status-bar text and colour for one replayed CPI."""
    mti = f"MTI {'on' if a_pos['mti'] else 'off'}" + (
        " (forced)" if a_pos["mti_forced"] else ""
    )
    n = a_pos["n_replayed"]
    if a_pos["match"] is None:
        return f"{mti} | replayed {n}, no live detections saved for this CPI", None
    text = f"{mti} | replayed {n}, live {a_pos['n_recorded']}"
    if a_pos["match"]:
        return text + ": identical", "#4caf50"
    if a_pos["mti_forced"]:
        return text + ": differ (MTI forced)", None
    if a_noisy:
        return text + ": differ (loopback noise is redrawn on replay)", None
    return text + ": DIFFER", "#ff9800"


# ===================================================================================
def build_app():
    """Display, player bar and worker, wired up: main() minus the event loop.
    Needs a QApplication."""
    display = gui.RadarDisplay(a_config=config.RadarConfig())  # until a session loads
    display.set_playback_mode()
    bar = gui.PlayerBar(a_start_dir=recorder.DEFAULT_ROOT)
    display.addToolBar(Qt.TopToolBarArea, bar)
    display.set_recorded_visible(bar.recorded_box.isChecked())

    worker = PlaybackWorker()
    noisy = False

    def on_loaded(a_info):
        nonlocal noisy
        noisy = a_info["noisy"]
        display.set_config(a_info["cfg"])
        display.show_fake_targets(a_info["fake_targets"])
        bar.on_loaded(a_info)

    def on_position(a_pos):
        bar.on_position(a_pos["block"], a_pos["t"])
        display.show_mti(a_pos["mti"])
        display.set_status(*_status(a_pos, noisy))

    def on_error(a_text):
        print(a_text)
        display.set_status(a_text.splitlines()[0], "#ff5252")

    # Worker -> GUI: slots run on the GUI thread (Qt queues cross-thread emits)
    worker.results.connect(
        lambda rd_map_db_up, rd_map_db_down, detections, ranges, velocities: display.update(
            rd_map_db_up, rd_map_db_down, ranges, velocities, detections
        )
    )
    worker.signals.connect(
        lambda rx_spec, if_spec, t, f: display.update_signals(rx_spec, if_spec, t, f)
    )
    worker.recorded.connect(display.update_recorded)
    worker.loaded.connect(on_loaded)
    worker.position.connect(on_position)
    worker.playing.connect(bar.on_playing)
    worker.error.connect(on_error)

    # GUI -> worker
    bar.open_requested.connect(worker.open)
    bar.play_requested.connect(worker.set_playing)
    bar.step_requested.connect(worker.step)
    bar.seek_requested.connect(worker.seek)
    bar.speed_changed.connect(worker.set_speed)
    bar.mti_mode_changed.connect(worker.set_mti_mode)
    bar.show_recorded_changed.connect(display.set_recorded_visible)
    return display, bar, worker


def main():
    ap = argparse.ArgumentParser(description="Play a recorded K2 session in the GUI")
    ap.add_argument("session", nargs="?", type=Path, help="session directory")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    display, bar, worker = build_app()
    display.show()
    worker.start()
    if args.session is not None:
        worker.open(args.session)
    app.aboutToQuit.connect(worker.stop)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
