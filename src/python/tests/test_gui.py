"""
tests/test_gui.py

Board-free GUI smoke tests: RadarDisplay in every mode, set_config across mode
changes, the config form, the fake-target table, the record box and the playback
app (offline.playback.build_app) on a temp session, and the intake / draw split
(update() draws nothing, the timer / draw_now() / a tab change do). Offscreen Qt. PySide prints a
slot exception and carries on, so sys.excepthook is recorded and every test
asserts it saw nothing. Run as __main__.
"""

import os

# Before ANY Qt import, direct or through common.gui / offline.playback
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import dataclasses
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

import common.config as config
from common import gui
from offline import playback
from online.target_sim import TargetSim
from tests.test_recorder import fabric_cfg, if_blocks, record_session

SEED = 0
APP = QApplication.instance() or QApplication(sys.argv)

# A slot exception does not propagate: record it here, assert per test
EXCEPTIONS = []


def _hook(a_type, a_value, a_tb):
    EXCEPTIONS.append("".join(traceback.format_exception(a_type, a_value, a_tb)))


sys.excepthook = _hook

N_DOPPLER, N_RANGE = 128, 707
N_TIME, N_FREQ = 32, 64

# (name, config fields); the four display modes
MODES = [
    ("sawtooth", dict()),
    ("triangle", dict(TRIANGLE_EN=True)),
    ("tdm", dict(TDM_EN=True)),
    ("triangle+tdm", dict(TRIANGLE_EN=True, TDM_EN=True)),
]


def mode_cfg(**a_fields):
    # Fabric: the 128 x 707 map of the real GUI load; loopback off, nothing random
    return config.RadarConfig(
        FABRIC_DECHIRP_EN=True, SDR_LOOPBACK_EN=False, **a_fields
    )


def pump(a_ms=0):
    """Run the event loop for a_ms (queued signals, paint)."""
    end = time.monotonic() + a_ms / 1000
    APP.processEvents()
    while time.monotonic() < end:
        APP.processEvents()


def wait_for(a_cond, a_what, a_timeout=10.0):
    """Pump the event loop until a_cond() holds; fail on the deadline."""
    end = time.monotonic() + a_timeout
    while time.monotonic() < end:
        APP.processEvents()
        if a_cond():
            return
        time.sleep(0.002)  # yield the GIL to the worker, not the wait itself
    raise AssertionError(f"timeout waiting for {a_what}")


def no_exceptions():
    assert not EXCEPTIONS, "slot exception:\n" + "\n".join(EXCEPTIONS)


def frame(a_cfg, a_n_det, a_rng):
    """One synthetic CPI shaped like process_cpi's output."""
    ranges = np.linspace(0, a_cfg.MAX_RANGE, N_RANGE)
    vels = np.linspace(-a_cfg.MAX_VELOCITY, a_cfg.MAX_VELOCITY, N_DOPPLER)

    def rd_map():
        rd = a_rng.normal(-60, 4, (N_DOPPLER, N_RANGE)).astype(np.float32)
        for _ in range(5):
            rd[a_rng.integers(2, N_DOPPLER - 2), a_rng.integers(2, N_RANGE - 2)] = -5.0
        return rd

    up = rd_map()
    down = rd_map() if a_cfg.TRIANGLE_EN else None
    kinds = ["both", "up", "down"] if a_cfg.TRIANGLE_EN else ["up"]
    targets = []
    for _ in range(a_n_det):
        t = {
            "r": float(a_rng.uniform(1, a_cfg.MAX_RANGE)),
            "v": float(a_rng.uniform(-a_cfg.MAX_VELOCITY, a_cfg.MAX_VELOCITY)),
            "kind": kinds[int(a_rng.integers(len(kinds)))],
            "snr_db": float(a_rng.uniform(10, 40)),
        }
        if a_cfg.TDM_EN:
            t["theta"] = float(a_rng.uniform(-40, 40))
        targets.append(t)
    return up, down, ranges, vels, targets


def spec_args(a_rng):
    t = np.linspace(0, 6.4e-3, N_TIME)
    f = np.linspace(-28e6, 28e6, N_FREQ)
    spec = lambda: a_rng.normal(-70, 3, (N_TIME, N_FREQ)).astype(np.float32)
    return spec(), spec(), t, f


def make_display(a_cfg):
    return gui.RadarDisplay(a_config=a_cfg)


def feed(a_display, a_cfg, a_rng, a_counts=(0, 1, 30)):
    tabs = a_display.centralWidget()
    for n in a_counts:
        up, down, ranges, vels, targets = frame(a_cfg, n, a_rng)
        a_display.update(up, down, ranges, vels, targets)
        a_display.draw_now()  # update() only takes data in
        pump()
    rx, ifs, t, f = spec_args(a_rng)
    a_display.update_signals(rx, ifs, t, f)
    a_display.update_signals(rx, None, t, f)
    # Signal, History: drawn when shown; back to Tracks
    for tab in (a_display._signal_tab, a_display._history_tab, a_display._tracks_tab):
        tabs.setCurrentWidget(tab)
        a_display.draw_now()
    pump()


def check_visible(a_display, a_cfg):
    """Cheap visible-state asserts that follow from the config."""
    # isHidden, not isVisible: tab pages and an unshown window hide children anyway
    assert (not a_display.xy_plot.isHidden()) == a_cfg.TDM_EN
    assert a_display.track_view.isColumnHidden(3) == (not a_cfg.TDM_EN)
    assert (not a_display.rd_down_plot.isHidden()) == a_cfg.TRIANGLE_EN
    assert (not a_display.if_spec_plot.isHidden()) == (not a_cfg.FABRIC_DECHIRP_EN)


# ===================================================================================
def test_update_all_modes():
    # update() with 0, 1 and many detections, update_signals() with and without IF
    rng = np.random.default_rng(SEED)
    for name, fields in MODES:
        cfg = mode_cfg(**fields)
        d = make_display(cfg)
        d.resize(1200, 700)
        d.show()
        feed(d, cfg, rng)
        check_visible(d, cfg)
        d.close()
    no_exceptions()


def test_update_software_mode():
    # Software dechirp: the IF spectrogram plot exists
    rng = np.random.default_rng(SEED)
    cfg = config.RadarConfig(FABRIC_DECHIRP_EN=False, SDR_LOOPBACK_EN=False)
    d = make_display(cfg)
    assert not d.if_spec_plot.isHidden()
    feed(d, cfg, rng)
    d.close()
    no_exceptions()


def test_set_config_mode_changes():
    # Every mode change both ways, one flag at a time, then update() again
    rng = np.random.default_rng(SEED)
    base = mode_cfg()
    d = make_display(base)
    d.show()
    cfgs = [base]
    for field in ("TDM_EN", "TRIANGLE_EN", "FABRIC_DECHIRP_EN"):
        flipped = dataclasses.replace(base, **{field: not getattr(base, field)})
        cfgs += [flipped, base]
    # And the combined jumps, there and back
    both = dataclasses.replace(base, TDM_EN=True, TRIANGLE_EN=True)
    soft = dataclasses.replace(both, FABRIC_DECHIRP_EN=False)
    cfgs += [both, soft, base, soft, both, base]
    for cfg in cfgs:
        d.set_config(cfg)
        check_visible(d, cfg)
        feed(d, cfg, rng, a_counts=(0, 5))
        if not cfg.TRIANGLE_EN:
            assert d.rd_down_image.image is None, "stale down map after sawtooth"
    d.close()
    no_exceptions()


def test_reconfigure_and_revert():
    cfg = mode_cfg()
    d = make_display(cfg)
    got = []
    d.reconfigure_requested.connect(got.append)
    spin = d._cfg_widgets["CHIRP_REPS"]
    assert spin.value() == cfg.CHIRP_REPS
    spin.setValue(64)
    d.reconfigure()
    assert len(got) == 1 and isinstance(got[0], config.RadarConfig)
    assert got[0].CHIRP_REPS == 64
    assert not d.re_cfg_button.isEnabled(), "disabled until the worker acks"
    # The worker refused: the form goes back to the old config
    d.on_reconfigure_done(False, cfg)
    assert d.re_cfg_button.isEnabled()
    assert spin.value() == cfg.CHIRP_REPS
    assert d._config == cfg
    # An ack of success keeps what the form has
    spin.setValue(48)
    d.reconfigure()
    d.on_reconfigure_done(True, got[-1])
    assert spin.value() == 48 and d.re_cfg_button.isEnabled()
    assert len(got) == 2 and got[1].CHIRP_REPS == 48
    d.close()
    no_exceptions()


def test_fake_target_table():
    d = make_display(mode_cfg())
    got = []
    d.fake_targets_changed.connect(got.append)
    t = d.fake_targets_table
    cols = [c[0] for c in d.FAKE_TGT_COLUMNS]

    d._fake_target_add_row()
    assert len(got) == 1 and len(got[-1]) == 1
    assert list(got[-1][0]) == cols
    assert got[-1][0]["r0"] == 500.0 and got[-1][0]["amp"] == 0.1

    t.item(0, 0).setText("123.5")  # edit r0
    assert len(got) == 2 and got[-1][0]["r0"] == 123.5

    n = len(got)
    t.item(0, 1).setText("abc")  # bad cell: no emit, marked
    assert len(got) == n, "a bad cell must not emit"
    assert t.item(0, 1).background().color().name() == "#802020"
    t.item(0, 1).setText("-7")  # fixed: emits again, mark cleared
    assert len(got) == n + 1 and got[-1][0]["v0"] == -7.0
    assert t.item(0, 1).background().color().name() != "#802020"

    d._fake_target_add_row()
    assert len(got[-1]) == 2
    t.setCurrentCell(0, 0)
    d._fake_target_del_row()
    assert len(got[-1]) == 1 and got[-1][0]["r0"] == 500.0  # the second row is left
    t.setCurrentCell(0, 0)
    d._fake_target_del_row()
    assert got[-1] == [] and t.rowCount() == 0
    n = len(got)
    d._fake_target_del_row()  # nothing selected: nothing to delete
    assert len(got) == n

    # show_fake_targets fills without emitting
    d.show_fake_targets([dict(r0=10.0, v0=1.0, a0=0.0, theta0=0.0, duration=5.0, amp=1.0)])
    assert t.rowCount() == 1 and len(got) == n
    d.close()
    no_exceptions()


def test_on_recording():
    d = make_display(mode_cfg())
    emitted = []
    d.record_changed.connect(emitted.append)
    d.on_recording(Path("/data/sessions/2026-11-15_120000"))
    assert d.record_box.isChecked()
    assert d.record_box.text() == "Record (2026-11-15_120000)"
    assert d.record_box.toolTip().endswith("2026-11-15_120000")
    d.on_recording(None)
    assert not d.record_box.isChecked()
    assert d.record_box.text() == "Record" and d.record_box.toolTip() == ""
    assert emitted == [], "on_recording must not re-emit record_changed"
    d.close()
    no_exceptions()


def shown_up(a_display):
    """The up-chirp RD image of the Tracks tab (None: nothing drawn yet)."""
    return a_display.det_image.image


def test_intake_does_not_draw():
    rng = np.random.default_rng(SEED)
    cfg = mode_cfg(TDM_EN=True)
    d = make_display(cfg)
    d.show()
    f1 = frame(cfg, 3, rng)
    f2 = frame(cfg, 7, rng)
    d.update(*f1)
    d.draw_now()
    assert np.array_equal(shown_up(d), f1[0])
    assert d.scatter_both.data["x"].size == 3
    d.update(*f2)  # intake: no event loop turn, no draw
    assert np.array_equal(shown_up(d), f1[0]) and d.scatter_both.data["x"].size == 3
    d.draw_now()
    assert np.array_equal(shown_up(d), f2[0]) and d.scatter_both.data["x"].size == 7
    d.close()
    no_exceptions()


def test_timer_draws():
    rng = np.random.default_rng(SEED)
    cfg = mode_cfg()
    d = make_display(cfg)
    d.show()
    f1 = frame(cfg, 4, rng)
    d.update(*f1)
    assert shown_up(d) is None
    wait_for(lambda: shown_up(d) is not None, "the timer to draw the frame")
    assert np.array_equal(shown_up(d), f1[0])
    # A newer frame replaces it by itself
    f2 = frame(cfg, 9, rng)
    d.update(*f2)
    wait_for(lambda: np.array_equal(shown_up(d), f2[0]), "the second frame")
    d.close()
    no_exceptions()


def test_tab_change_draws_at_once():
    rng = np.random.default_rng(SEED)
    cfg = mode_cfg()
    d = make_display(cfg)
    d.show()
    tabs = d.centralWidget()
    f = frame(cfg, 3, rng)
    d.update(*f)
    d.update_signals(*spec_args(rng))
    # The Tracks tab is current: Signal and History take data in, draw nothing
    assert d.rti_image.image is None and d.rx_spec_image.image is None
    assert d.rd_up_image.image is None
    tabs.setCurrentWidget(d._signal_tab)
    assert d.rx_spec_image.image is not None, "drawn on the tab change, no tick"
    assert d.rd_up_image.image is not None, "RD maps drawn with the Signal tab"
    assert d.rti_image.image is None
    tabs.setCurrentWidget(d._history_tab)
    assert d.rti_image.image is not None
    d.update(*frame(cfg, 1, rng))  # taken in while History shows: not Tracks-drawn
    tabs.setCurrentWidget(d._tracks_tab)
    assert np.array_equal(shown_up(d), d._frame[0]), "Tracks catches up on show"
    d.close()
    no_exceptions()


def test_set_config_drops_pending():
    rng = np.random.default_rng(SEED)
    cfg = mode_cfg()
    d = make_display(cfg)
    d.show()
    d.update(*frame(cfg, 3, rng))
    d.draw_now()
    d.update(*frame(cfg, 3, rng))  # pending, then the config changes
    d.update_signals(*spec_args(rng))
    # A smaller map: the pending frame would not fit the new axes
    new = dataclasses.replace(cfg, TDM_EN=True, CHIRP_REPS=64)
    d.set_config(new)
    assert d._frame is None and d._spec_rx is None
    pump(120)  # ticks with nothing to draw
    up = rng.normal(-60, 4, (64, 300)).astype(np.float32)
    ranges = np.linspace(0, new.MAX_RANGE, 300)
    vels = np.linspace(-new.MAX_VELOCITY, new.MAX_VELOCITY, 64)
    d.update(up, None, ranges, vels, [{"r": 50.0, "v": 1.0, "kind": "up", "theta": 5.0}])
    wait_for(lambda: shown_up(d).shape == (64, 300), "the new map size")
    d.draw_now()
    d.centralWidget().setCurrentWidget(d._history_tab)
    d.draw_now()
    d.close()
    no_exceptions()


def slot_of(a_display, a_widget):
    left = a_display._left_lay.indexOf(a_widget) >= 0
    right = a_display._right_lay.indexOf(a_widget) >= 0
    assert left != right or not (left or right), "widget in both slots"
    return "left" if left else "right" if right else None


def check_layout(a_display, a_cfg):
    d = a_display
    names = ("xy_plot", "det_plot", "track_view")
    slots = {n: slot_of(d, getattr(d, n)) for n in names}
    if a_cfg.TDM_EN:
        assert slots == dict(xy_plot="left", det_plot="right", track_view="right")
        assert d._right_lay.indexOf(d.det_plot) < d._right_lay.indexOf(d.track_view)
        assert not d.xy_plot.isHidden()
    else:
        assert slots == dict(xy_plot=None, det_plot="left", track_view="right")
        assert d.xy_plot.isHidden()
        assert d._right_lay.count() == 1
    assert not d.det_plot.isHidden() and not d.track_view.isHidden()
    assert d.track_view.isColumnHidden(d.TRACK_AZ_COL) == (not a_cfg.TDM_EN)
    heads = [
        d.track_model.headerData(c, gui.Qt.Horizontal) for c in range(6)
    ]
    assert heads == ["ID", "r [m]", "v [m/s]", "az [deg]", "SNR [dB]", "age [s]"]


def test_tab_order():
    d = make_display(mode_cfg())
    tabs = d.centralWidget()
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    assert titles == ["Tracks", "Signal", "History", "Config"]
    for i, w in enumerate((d._tracks_tab, d._signal_tab, d._history_tab, d._config_tab)):
        assert tabs.indexOf(w) == i
    d.close()
    no_exceptions()


def test_layout_modes():
    rng = np.random.default_rng(SEED)
    for name, fields in MODES:
        cfg = mode_cfg(**fields)
        d = make_display(cfg)
        d.resize(1200, 700)
        d.show()
        check_layout(d, cfg)
        feed(d, cfg, rng, a_counts=(5,))
        d.close()
    no_exceptions()


def test_relayout_cycles():
    # TDM off -> on -> off -> on, update + draw between; same widget objects
    rng = np.random.default_rng(SEED)
    off = mode_cfg()
    on = mode_cfg(TDM_EN=True)
    d = make_display(off)
    d.show()
    plots = (d.det_plot, d.xy_plot, d.track_view)
    for cfg in (off, on, off, on, off, on):
        d.set_config(cfg)
        check_layout(d, cfg)
        d.update(*frame(cfg, 10, rng))
        d.draw_now()
        pump()
        assert d.scatter_both.data["x"].size == 10
        assert (d.det_plot, d.xy_plot, d.track_view) == plots
    d.close()
    no_exceptions()


def test_toolbar_and_detections_toggle():
    rng = np.random.default_rng(SEED)
    cfg = mode_cfg(TRIANGLE_EN=True, TDM_EN=True)
    d = make_display(cfg)
    d.show()
    assert d.dets_box.isChecked() and not d.tentative_box.isChecked()
    assert not d.xs_toggle.isChecked()
    up, down, ranges, vels, targets = frame(cfg, 12, rng)
    for t in targets:
        t["kind"] = "both"
    targets[0]["kind"] = "up"
    d.update(up, down, ranges, vels, targets)
    d.update_recorded([{"r": 50.0, "v": 1.0, "kind": "both"}])
    d.set_recorded_visible(True)
    d.draw_now()
    assert d.scatter_both.isVisible() and d.xy_scatter.isVisible()
    assert d.scatter_recorded.isVisible()
    # Toggle with data already taken in: dirty flag, drawn by the next draw
    d.dets_box.setChecked(False)
    assert d._tracks_dirty
    d._draw_current(a_now=False)
    for item in (d.scatter_both, d.scatter_up, d.scatter_down, d.xy_scatter):
        assert not item.isVisible()
    assert d.scatter_recorded.isVisible(), "rings stay"
    d.dets_box.setChecked(True)
    d._draw_current(a_now=False)
    assert d.scatter_both.isVisible() and d.xy_scatter.isVisible()
    # Detections stay off across a new frame
    d.dets_box.setChecked(False)
    d.update(up, down, ranges, vels, targets)
    d.draw_now()
    assert not d.scatter_both.isVisible() and not d.xy_scatter.isVisible()
    # Toolbar signals still wired
    seen = []
    d.mti_signal_changed.connect(seen.append)
    d.mti_en_box.setChecked(not d.mti_en_box.isChecked())
    assert len(seen) == 1
    d.close()
    no_exceptions()


def test_signal_tab_draws_maps_only_when_current():
    rng = np.random.default_rng(SEED)
    cfg = mode_cfg(TRIANGLE_EN=True)
    d = make_display(cfg)
    d.show()
    f = frame(cfg, 3, rng)
    d.update(*f)
    d.draw_now()  # Tracks tab current
    assert d.rd_up_image.image is None and d.rd_down_image.image is None
    d.centralWidget().setCurrentWidget(d._signal_tab)
    assert np.array_equal(d.rd_up_image.image, f[0])
    assert np.array_equal(d.rd_down_image.image, f[1])
    f2 = frame(cfg, 3, rng)
    d.update(*f2)  # taken in, not drawn while Signal shows? it is: tick draws
    d.draw_now()
    assert np.array_equal(d.rd_up_image.image, f2[0])
    d.centralWidget().setCurrentWidget(d._tracks_tab)
    f3 = frame(cfg, 3, rng)
    d.update(*f3)
    d.draw_now()
    assert np.array_equal(d.rd_up_image.image, f2[0]), "Signal maps not redrawn"
    d.close()
    no_exceptions()


def test_playback_mode_toolbar():
    d = make_display(mode_cfg())
    d.show()
    d.set_playback_mode()
    assert not d.mti_en_box.isHidden() and not d.mti_en_box.isEnabled()
    assert d.record_box.isHidden() and d.record_notes.isHidden()
    assert not d.dets_box.isHidden() and not d.xs_toggle.isHidden()
    d.update_recorded([{"r": 50.0, "v": 1.0, "kind": "up"}])
    d.set_recorded_visible(True)
    d.draw_now()
    assert d.scatter_recorded.isVisible(), "rings stay"
    d.show_mti(True)
    assert d.mti_en_box.isChecked()
    d.close()
    no_exceptions()


def pixel_of(a_image, a_v, a_r):
    """(col, row) of the image pixel holding data point (v, r), as floats."""
    p = a_image.mapFromParent(gui.QPointF(a_v, a_r))
    return p.x(), p.y()


def test_rd_alignment():
    # A detection at (v[i], r[j]) must land on the centre of image cell (i, j)
    rng = np.random.default_rng(SEED)
    for name, fields in MODES[:2]:
        cfg = mode_cfg(**fields)
        d = make_display(cfg)
        d.show()
        _, _, ranges, vels, _ = frame(cfg, 0, rng)
        dv, dr = vels[1] - vels[0], ranges[1] - ranges[0]
        for i, j in [(0, 0), (N_DOPPLER - 1, N_RANGE - 1), (0, N_RANGE - 1),
                     (N_DOPPLER // 2, N_RANGE // 3), (17, 400)]:
            up = np.full((N_DOPPLER, N_RANGE), -70.0, np.float32)
            up[i, j] = -3.0
            down = up.copy() if cfg.TRIANGLE_EN else None
            tgt = [{"r": float(ranges[j]), "v": float(vels[i]), "kind": "up"}]
            d.update(up, down, ranges, vels, tgt)
            d.draw_now()
            x, y = pixel_of(d.det_image, vels[i], ranges[j])
            assert (int(np.floor(x)), int(np.floor(y))) == (i, j), (name, i, j, x, y)
            assert abs(x - (i + 0.5)) < 1e-6 and abs(y - (j + 0.5)) < 1e-6
            # Same in data coordinates: cell centre within half a bin of the dot
            c = d.det_image.mapToParent(gui.QPointF(i + 0.5, j + 0.5))
            assert abs(c.x() - vels[i]) < dv / 2 and abs(c.y() - ranges[j]) < dr / 2
            dot = d.scatter_up if cfg.TRIANGLE_EN else d.scatter_both
            assert abs(dot.data["x"][0] - vels[i]) < dv / 2
            # Signal tab maps use the same rect; down map is Doppler-mirrored
            d.centralWidget().setCurrentWidget(d._signal_tab)
            d.draw_now()
            x, y = pixel_of(d.rd_up_image, vels[i], ranges[j])
            assert (int(np.floor(x)), int(np.floor(y))) == (i, j)
            if down is not None:
                x, y = pixel_of(d.rd_down_image, vels[N_DOPPLER - 1 - i], ranges[j])
                assert (int(np.floor(x)), int(np.floor(y))) == (i, j), "down mirror"
            d.centralWidget().setCurrentWidget(d._tracks_tab)
        d.close()
    no_exceptions()


def test_playback_paused_step_shows_cpi():
    cfg = fabric_cfg()
    blocks = if_blocks(cfg, 6, a_ranges=(150.0, 420.0))
    with tempfile.TemporaryDirectory() as tmp:
        session_dir, _ = record_session(Path(tmp), cfg, blocks, TargetSim())
        display, bar, worker = playback.build_app()
        errors, positions, ups = [], [], []
        worker.error.connect(errors.append)
        worker.position.connect(positions.append)
        worker.results.connect(lambda up, *a_rest: ups.append(up))
        display.show()
        worker.start()
        try:
            worker.open(session_dir)
            wait_for(lambda: positions, "open")
            wait_for(lambda: np.array_equal(shown_up(display), ups[-1]), "block 0")
            for step in (1, 1, -1):
                n = len(ups)
                worker.step(step)  # paused: one frame, no more arrive
                wait_for(lambda: len(ups) > n, "the stepped frame")
                want = ups[-1]
                wait_for(
                    lambda: np.array_equal(shown_up(display), want),
                    "the paused step to be drawn",
                )
                assert not errors, errors
        finally:
            worker.stop()
        pump(50)
        display.close()
    no_exceptions()


def test_playback():
    cfg = fabric_cfg()
    blocks = if_blocks(cfg, 6, a_ranges=(150.0, 420.0))
    with tempfile.TemporaryDirectory() as tmp:
        session_dir, _ = record_session(Path(tmp), cfg, blocks, TargetSim())
        display, bar, worker = playback.build_app()
        errors, positions, results = [], [], []
        worker.error.connect(errors.append)
        worker.position.connect(positions.append)
        worker.results.connect(lambda *a_args: results.append(1))
        loaded = []
        worker.loaded.connect(loaded.append)
        display.show()
        worker.start()
        try:
            n_seen = lambda: len(positions)

            def command(a_fn, a_what):
                n = n_seen()
                a_fn()
                wait_for(lambda: n_seen() > n, a_what)
                assert not errors, errors
                return positions[-1]

            worker.open(session_dir)
            wait_for(lambda: loaded and positions, "open")
            assert positions[-1]["block"] == 0
            assert bar.slider.maximum() == len(blocks) - 1

            assert command(lambda: worker.step(1), "step")["block"] == 1
            assert command(lambda: worker.step(1), "step")["block"] == 2
            assert command(lambda: worker.step(-1), "step back")["block"] == 1
            assert command(lambda: worker.seek(4), "seek")["block"] == 4
            assert command(lambda: worker.seek(0), "seek back")["block"] == 0

            # MTI forced on, off, then back to the recording
            p = command(lambda: worker.set_mti_mode(True), "mti on")
            assert p["mti"] is True and p["mti_forced"]
            assert display.mti_en_box.isChecked()
            p = command(lambda: worker.set_mti_mode(False), "mti off")
            assert p["mti"] is False and p["mti_forced"]
            p = command(lambda: worker.set_mti_mode(None), "mti recorded")
            assert not p["mti_forced"]

            # Play to the end as fast as possible, it stops by itself
            worker.set_speed(0.0)
            worker.set_playing(True)
            wait_for(lambda: positions[-1]["block"] == len(blocks) - 1, "play to end")
            pump(100)
            assert worker.isRunning() and not errors, errors
            last = n_seen()
            pump(100)
            assert n_seen() == last, "playback must stop at the end"
            assert len(results) >= len(positions) - 1
        finally:
            worker.stop()
        pump(50)
        assert not worker.isRunning()
        assert not errors, errors
        display.close()
    no_exceptions()


if __name__ == "__main__":
    tests = [
        test_update_all_modes,
        test_update_software_mode,
        test_set_config_mode_changes,
        test_reconfigure_and_revert,
        test_fake_target_table,
        test_on_recording,
        test_intake_does_not_draw,
        test_timer_draws,
        test_tab_change_draws_at_once,
        test_set_config_drops_pending,
        test_tab_order,
        test_layout_modes,
        test_relayout_cycles,
        test_toolbar_and_detections_toggle,
        test_signal_tab_draws_maps_only_when_current,
        test_playback_mode_toolbar,
        test_rd_alignment,
        test_playback_paused_step_shows_cpi,
        test_playback,
    ]
    failures = 0
    for t in tests:
        del EXCEPTIONS[:]
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")

    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
