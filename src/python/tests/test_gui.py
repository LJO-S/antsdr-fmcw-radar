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
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
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


def n_dots(a_display, a_dots=None):
    """Fading dots drawn on the (r, v) plot (cells of the dot image)."""
    return (a_dots or a_display.det_dots).n


def dots_visible(a_display, a_dots=None):
    return (a_dots or a_display.det_dots).isVisible()


def test_intake_does_not_draw():
    rng = np.random.default_rng(SEED)
    cfg = mode_cfg(TDM_EN=True)
    d = make_display(cfg)
    d.show()
    f1 = frame(cfg, 3, rng)
    f2 = frame(cfg, 7, rng)
    d.update(*f1, a_t=10.0)
    d.draw_now()
    assert np.array_equal(shown_up(d), f1[0])
    assert n_dots(d) == 3
    d.update(*f2, a_t=20.0)  # intake: no event loop turn, no draw
    assert np.array_equal(shown_up(d), f1[0]) and n_dots(d) == 3
    d.draw_now()
    assert np.array_equal(shown_up(d), f2[0]) and n_dots(d) == 7
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
        assert n_dots(d) == 10
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
    assert dots_visible(d) and dots_visible(d, d.xy_dots)
    assert d.scatter_recorded.isVisible()
    # Toggle with data already taken in: dirty flag, drawn by the next draw
    d.dets_box.setChecked(False)
    assert d._tracks_dirty
    d._draw_current(a_now=False)
    assert not dots_visible(d) and not dots_visible(d, d.xy_dots)
    for item in (d.scatter_up, d.scatter_down, d.xy_scatter):
        assert not item.isVisible()
    assert d.scatter_recorded.isVisible(), "rings stay"
    d.dets_box.setChecked(True)
    d._draw_current(a_now=False)
    assert dots_visible(d) and dots_visible(d, d.xy_dots)
    # Detections stay off across a new frame
    d.dets_box.setChecked(False)
    d.update(up, down, ranges, vels, targets)
    d.draw_now()
    assert not dots_visible(d) and not dots_visible(d, d.xy_dots)
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
        d.xs_toggle.setChecked(True)  # triangle: single-ramp crosses
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
            if cfg.TRIANGLE_EN:
                assert abs(d.scatter_up.data["x"][-1] - vels[i]) < dv / 2
            else:
                # Fading dot: trail point within half a bin, its image cell holds it
                v_dot, r_dot = d._trail.arrays()[0][-1]
                assert abs(v_dot - vels[i]) < dv / 2 and abs(r_dot - ranges[j]) < dr / 2
                nx, ny = d.det_dots.image.shape
                vmax, rmax = cfg.MAX_VELOCITY, cfg.MAX_RANGE
                cell = (
                    min(int((v_dot + vmax) / (2 * vmax) * nx), nx - 1),
                    min(int(r_dot / rmax * ny), ny - 1),
                )
                assert d.det_dots.image[cell] == 4
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


# ===================================================================================
# Track layer (common/gui_tracks.py)
# ===================================================================================
def snap(a_id, a_r, a_v, a_theta=None, a_age=5, a_num=None, a_status="confirmed",
         a_coasting=False, a_birth=0.0, **a_extra):
    """A Track.snapshot() dict."""
    d = {
        "id": a_id, "num": a_id if a_num is None else a_num, "status": a_status,
        "r": a_r, "v": a_v, "theta": a_theta, "birth_t": a_birth, "snr_db": 20.0,
        "age": a_age, "nis": None, "kin_ok": True, "coasting": a_coasting,
    }
    d.update(a_extra)
    return d


def test_store_tail_trim():
    from common import gui_tracks as gt

    st = gt.TrackStore(a_v_limit=20.0)
    for k in range(200):  # 20 s at 10 Hz, tail buffer wraps and grows
        t = k * 0.1
        st.update([snap(0, 100.0 + k, 1.0, 10.0, a_age=k)], t)
        tail = st.tracks[0].tail
        assert tail[-1, 0] == t
        assert tail[0, 0] >= t - gt.TAIL_S - 1e-9
    assert len(st.tracks[0].tail) == 21  # 2.0 s at 0.1 s steps, both ends
    assert abs(st.tracks[0].age_s - 19.9) < 1e-9
    assert np.array_equal(st.tracks[0].tail_rv()[-1], [1.0, 299.0])


def test_store_restart_and_clear():
    from common import gui_tracks as gt

    st = gt.TrackStore(a_v_limit=20.0)
    for k in range(10):
        st.update([snap(0, 100.0, 1.0, a_age=k, a_birth=0.0)], k * 0.1)
    assert len(st.tracks[0].tail) == 10
    old = st.tracks[0]
    st.update([snap(0, 50.0, 1.0, a_age=2, a_birth=0.8)], 1.0)  # age dropped
    assert st.tracks[0] is not old and len(st.tracks[0].tail) == 1
    # Same id, same age but a new birth_t: also a different track
    old = st.tracks[0]
    st.update([snap(0, 50.0, 1.0, a_age=3, a_birth=0.9)], 1.1)
    assert st.tracks[0] is not old
    # Time backwards clears the whole store, then the list is taken in
    st.update([snap(0, 1.0, 1.0), snap(1, 2.0, 1.0)], 0.5)
    assert set(st.tracks) == {0, 1} and all(len(s.tail) == 1 for s in st.tracks.values())
    # Absent track is gone, empty list clears
    st.update([snap(1, 2.0, 1.0, a_age=6)], 0.6)
    assert set(st.tracks) == {1}
    st.update([], None)
    assert not st.tracks and st.t is None
    # The same CPI again (paused re-render) replaces its point
    st.update([snap(3, 5.0, 1.0)], 2.0)
    st.update([snap(3, 6.0, 1.0)], 2.0)
    assert len(st.tracks[3].tail) == 1 and st.tracks[3].r == 6.0


def test_store_hysteresis():
    from common import gui_tracks as gt

    st = gt.TrackStore()

    def cls_of(v, k=[0]):
        k[0] += 1
        st.update([snap(0, 100.0, v, a_age=k[0])], k[0] * 0.05)
        return st.tracks[0].cls

    assert cls_of(-0.4) == gt.NEUTRAL  # not yet in
    assert cls_of(-0.6) == gt.APPROACHING
    for v in (-0.4, -0.6) * 5:  # no flicker
        assert cls_of(v) == gt.APPROACHING
    assert cls_of(-0.31) == gt.APPROACHING
    assert cls_of(-0.29) == gt.NEUTRAL  # out below 0.3
    assert cls_of(0.4) == gt.NEUTRAL
    assert cls_of(0.6) == gt.RECEDING
    assert cls_of(0.4) == gt.RECEDING and cls_of(0.31) == gt.RECEDING
    assert cls_of(0.2) == gt.NEUTRAL
    assert cls_of(0.6) == gt.RECEDING and cls_of(-0.6) == gt.APPROACHING  # straight over


def test_store_wrap_xy():
    from common import gui_tracks as gt

    st = gt.TrackStore(a_v_limit=10.0)
    for k, v in enumerate((9.0, 9.6, 10.4, 11.0)):  # raw v passes +10: wraps to -9.6
        st.update([snap(0, 100.0, v, a_age=k)], k * 0.1)
    s = st.tracks[0]
    assert abs(s.v - (-9.0)) < 1e-9
    rv = s.tail_rv()
    assert len(rv) == 5 and np.isnan(rv[2]).all(), "one NaN row breaks the line"
    assert np.isfinite(rv[[0, 1, 3, 4]]).all()
    assert abs(rv[3, 0] - (-9.6)) < 1e-9
    # No theta: not on the PPI, still on the (r, v) plot
    assert np.isnan(s.x) and np.isnan(s.y) and np.isnan(s.tail_xy()).all()
    # theta (degrees): x = r sin, y = r cos
    st.update([snap(1, 100.0, 1.0, 30.0)], 0.0)  # time backwards: new store
    s = st.tracks[1]
    assert abs(s.x - 50.0) < 1e-9 and abs(s.y - 100 * np.cos(np.radians(30))) < 1e-9
    # Snapshot x, y win when present (L5)
    st.update([snap(1, 100.0, 1.0, 30.0, x=7.0, y=8.0, a_age=6)], 0.1)
    assert (st.tracks[1].x, st.tracks[1].y) == (7.0, 8.0)
    # confirmed / tentative split
    st.update([snap(1, 1.0, 1.0), snap(2, 1.0, 1.0, a_status="tentative")], 0.2)
    assert [t.id for t in st.confirmed()] == [1] and [t.id for t in st.tentative()] == [2]


def test_trail_levels():
    from common import gui_tracks as gt

    tr = gt.DetectionTrail()
    pt = np.array([[1.0, 2.0]])
    none = np.empty((0, 2))
    tr.add(10.0, pt, pt)
    for dt, level in ((0.0, 4), (0.3, 3), (0.55, 2), (0.8, 1)):
        tr.add(10.0 + dt, none, none)
        rv, lv, xy, lx = tr.arrays()
        assert len(rv) == 1 and lv[0] == level and lx[0] == level, (dt, lv)
    tr.add(11.05, none, none)
    assert tr.n_points == 0 and len(tr.arrays()[0]) == 0
    # Oldest first: the newest overwrites on a shared cell
    tr.add(20.0, pt, pt)
    tr.add(20.1, pt, pt)
    assert list(tr.arrays()[1]) == [4, 4]
    tr.add(20.4, pt, pt)
    assert list(tr.arrays()[1]) == [3, 3, 4]
    for k in range(50):  # cap on points kept
        tr.add(30.0 + k * 0.001, np.ones((100, 2)), np.ones((100, 2)))
    assert tr.n_points <= gt.DET_MAX_POINTS
    tr.add(5.0, pt, pt)  # time backwards: restart
    assert tr.n_points == 1


def track_set(a_cfg):
    """Confirmed approaching / receding / slow / coasting + one tentative."""
    th = (lambda x: x) if a_cfg.TDM_EN else (lambda x: None)
    return [
        snap(0, 300.0, -3.0, th(-20.0), a_num=0),
        snap(1, 500.0, 2.0, th(10.0), a_num=1),
        snap(2, 700.0, 0.1, th(30.0), a_num=2),
        snap(3, 900.0, -4.0, th(0.0), a_num=3, a_coasting=True),
        snap(4, 400.0, 1.0, th(-30.0), a_status="tentative", a_num=None),
    ]


def head_colors(a_layer):
    return [(b.color().name(), b.color().alpha()) for b in a_layer.heads.data["brush"]]


def test_track_layer_modes_and_order():
    rng = np.random.default_rng(SEED)
    for name, fields in MODES:
        for order in ("tracks_first", "frame_first"):
            cfg = mode_cfg(**fields)
            d = make_display(cfg)
            d.show()
            trks = track_set(cfg)
            f = frame(cfg, 6, rng)
            t = 50.0
            if order == "tracks_first":
                d.update_tracks(trks, t)
                d.update(*f, a_t=t)
            else:
                d.update(*f, a_t=t)
                d.update_tracks(trks, t)
            d.draw_now()
            lay = d.det_layer
            xs, ys = lay.heads.data["x"], lay.heads.data["y"]
            assert np.allclose(xs, [-3.0, 2.0, 0.1, -4.0]), (name, list(xs))
            assert list(ys) == [300.0, 500.0, 700.0, 900.0]
            cols = head_colors(lay)
            assert cols == [
                ("#ff6b6b", 255), ("#4da3ff", 255), ("#7cfc9a", 255), ("#ff6b6b", 102),
            ], cols
            shown = [l for l in lay.labels if l.isVisible()]
            assert [l.toPlainText() for l in shown] == ["T0", "T1", "T2", "T3"]
            assert lay.tentative.data["x"].size == 0, "rings only with the toggle"
            d.tentative_box.setChecked(True)
            d.draw_now()
            assert list(lay.tentative.data["x"]) == [1.0]
            assert [l.isVisible() for l in lay.labels].count(True) == 4, "no ring label"
            for it in lay.tails:  # one point per track: single-point lines
                assert it.xData is not None
            if cfg.TDM_EN:
                xy = d.xy_layer
                th = np.radians([-20.0, 10.0, 30.0, 0.0])
                r = np.array([300.0, 500.0, 700.0, 900.0])
                assert np.allclose(xy.heads.data["x"], r * np.sin(th))
                assert np.allclose(xy.heads.data["y"], r * np.cos(th))
                assert head_colors(xy) == cols
                assert [l.toPlainText() for l in xy.labels if l.isVisible()] == [
                    "T0", "T1", "T2", "T3"
                ]
                assert np.allclose(xy.tentative.data["x"], 400 * np.sin(np.radians(-30)))
            else:
                assert d.xy_layer.heads.data["x"].size == 0
            # Tentative toggle off again
            d.tentative_box.setChecked(False)
            d.draw_now()
            assert lay.tentative.data["x"].size == 0
            # Empty list clears both layers
            d.update_tracks([], None)
            d.draw_now()
            for L in (d.det_layer, d.xy_layer):
                assert L.heads.data["x"].size == 0
                assert not any(l.isVisible() for l in L.labels)
            assert not d._store.tracks
            # Tracks again, then set_config clears
            d.update_tracks(trks, t + 0.05)
            d.draw_now()
            assert d.det_layer.heads.data["x"].size == 4
            d.set_config(cfg)
            assert not d._store.tracks and d.det_layer.heads.data["x"].size == 0
            assert not any(l.isVisible() for l in d.det_layer.labels)
            d.close()
    no_exceptions()


def test_track_layer_tails_and_labels():
    cfg = mode_cfg(TDM_EN=True)
    d = make_display(cfg)
    d.show()
    # 70 confirmed tracks: only 30 labels; moving ones grow a tail
    for k in range(40):
        t = 10.0 + 0.1 * k
        trks = [
            snap(i, 100.0 + 10 * i + k, -3.0 if i % 2 else 3.0, -30.0 + i % 60,
                 a_age=k, a_num=i)
            for i in range(70)
        ]
        d.update_tracks(trks, t)
    d.draw_now()
    lay = d.det_layer
    assert lay.heads.data["x"].size == 70
    assert [l.isVisible() for l in lay.labels].count(True) == 30
    recv = lay.tails[2]  # receding class: v = +3
    appr = lay.tails[1]
    n_pts = 21  # 2 s tail, 0.1 s steps
    assert np.isfinite(recv.xData).sum() == 35 * n_pts
    assert np.isfinite(appr.yData).sum() == 35 * n_pts
    assert lay.tails[0].xData.size == 0
    # A label follows its head
    first = next(l for l in lay.labels if l.isVisible())
    assert (first.pos().x(), first.pos().y()) == (3.0, 100.0 + 39)
    d.close()
    no_exceptions()


def test_detection_fade():
    cfg = mode_cfg()
    d = make_display(cfg)
    d.show()
    rng = np.random.default_rng(SEED)
    up, down, ranges, vels, _ = frame(cfg, 0, rng)
    one = [{"r": 100.0, "v": 1.0, "kind": "up"}]

    def step(a_t, a_dets):
        d.update(up, down, ranges, vels, a_dets, a_t=a_t)
        d.draw_now()
        return list(np.bincount(d.det_dots.image.ravel(), minlength=5)[1:])

    # Per level, oldest (1) .. newest (4)
    assert step(100.0, one) == [0, 0, 0, 1]
    assert step(100.3, []) == [0, 0, 1, 0]
    assert step(100.55, []) == [0, 1, 0, 0]
    assert step(100.8, []) == [1, 0, 0, 0]
    assert step(101.05, []) == [0, 0, 0, 0] and not dots_visible(d)
    # Cap
    many = [{"r": 100.0 + i, "v": 1.0, "kind": "up"} for i in range(100)]
    for k in range(30):
        step(200.0 + k * 0.01, many)
    assert n_dots(d) <= 1000 and d._trail.n_points <= 1000 and n_dots(d) > 0
    # Alphas step down, all four buckets' styles fixed
    alphas = list(d.det_dots.alphas[1:])  # level 1 (oldest) .. 4 (newest)
    assert alphas == sorted(alphas) and len(set(alphas)) == 4 and alphas[0] > 0
    d.dets_box.setChecked(False)
    d.draw_now()
    assert not dots_visible(d) and not dots_visible(d, d.xy_dots)
    # Grid: exists, has data, rebuilt per config
    n = d.det_grid.xData.size
    assert n > 0 and np.isfinite(d.det_grid.xData).any()
    d.set_config(mode_cfg(TRIANGLE_EN=True, TDM_EN=True))
    assert d.det_grid.xData.size > 0 and d.det_grid.zValue() < 0
    d.close()
    no_exceptions()


def test_grid_ticks():
    from common import gui_tracks as gt

    for lo, hi in [(0, 3000), (-19.3, 19.3), (0, 300), (-0.7, 0.7), (0, 1)]:
        t = gt.nice_ticks(lo, hi)
        assert 3 <= len(t) <= 12, (lo, hi, t)
        assert t[0] >= lo - 1e-9 and t[-1] <= hi + 1e-9
        assert np.allclose(np.diff(t), t[1] - t[0])


def test_adaptive_draw_interval():
    cfg = mode_cfg(TDM_EN=True)
    d = make_display(cfg)
    d.show()
    d._draw_timer.stop()  # ticks by hand
    d.update(*frame(cfg, 5, np.random.default_rng(SEED)))
    real = d._draw_tracks

    def tick(a_dirty=True):
        d._tracks_dirty = a_dirty
        d._on_tick()
        pump(20)  # the cost is taken by a posted zero timer, after the paint

    def slow(a_ms):
        def draw(*a):
            time.sleep(a_ms / 1000)
            return real(*a)
        d._draw_tracks = draw

    for _ in range(25):  # the first draw carries the window's first paint
        tick()
    assert d._draw_timer.interval() == d.DRAW_INTERVAL_MS, "cheap scene: the minimum"
    slow(30)
    for _ in range(8):
        tick()
    assert 110 <= d._draw_timer.interval() <= 150, d._draw_timer.interval()
    # Nothing dirty: nothing drawn, no cost taken, interval unchanged
    cost, interval = d._draw_cost_ms, d._draw_timer.interval()
    for _ in range(5):
        tick(False)
    assert d._draw_cost_ms == cost and d._draw_timer.interval() == interval
    # Back to cheap: returns to the minimum
    del d._draw_tracks
    for _ in range(20):
        tick()
    assert d._draw_timer.interval() == d.DRAW_INTERVAL_MS
    # Cap
    slow(100)
    for _ in range(10):
        tick()
    assert d._draw_timer.interval() == d.DRAW_INTERVAL_MAX_MS
    # draw_now / tab change stay immediate whatever the interval
    del d._draw_tracks
    up = d._frame[0]
    d.update(*frame(cfg, 2, np.random.default_rng(SEED + 1)))
    d.draw_now()
    assert np.array_equal(shown_up(d), d._frame[0]) and not np.array_equal(shown_up(d), up)
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


def test_playback_tracks():
    # A session recorded with TRACK_EN: tracks reach the display, a seek back clears
    cfg = fabric_cfg(TRACK_EN=True)
    blocks = if_blocks(cfg, 14, a_ranges=(150.0, 420.0))
    with tempfile.TemporaryDirectory() as tmp:
        session_dir, _ = record_session(Path(tmp), cfg, blocks, TargetSim())
        display, bar, worker = playback.build_app()
        errors, positions, track_emits = [], [], []
        worker.error.connect(errors.append)
        worker.position.connect(positions.append)
        worker.tracks.connect(lambda trks, t: track_emits.append((len(trks), t)))
        display.show()
        worker.start()
        try:
            def command(a_fn, a_what):
                n = len(positions)
                a_fn()
                wait_for(lambda: len(positions) > n, a_what)
                pump(30)
                assert not errors, errors
                return positions[-1]

            worker.open(session_dir)
            wait_for(lambda: positions, "open")
            for _ in range(3):
                command(lambda: worker.step(1), "step")
            pump(30)
            assert track_emits and track_emits[-1][1] is not None
            assert display._store.tracks, "tracks reached the store"
            assert display._store.t == track_emits[-1][1]
            display.draw_now()
            n_before = max(len(s.tail) for s in display._store.tracks.values())
            assert n_before > 1
            command(lambda: worker.seek(0), "seek back")
            # Tracker restarted on the jump: one point per track, nothing from before
            assert all(len(s.tail) <= 1 for s in display._store.tracks.values())
            display.draw_now()
            worker.set_speed(0.0)
            worker.set_playing(True)
            wait_for(lambda: positions[-1]["block"] == len(blocks) - 1, "play to end")
            pump(100)
            assert not errors, errors
            assert display._store.t == track_emits[-1][1]
        finally:
            worker.stop()
        pump(50)
        display.close()
    no_exceptions()


# ===================================================================================
# Track list and selection (WP6)
# ===================================================================================
YELLOW = "#ffd400"


def sel_display(a_fields, a_steps=6):
    """Shown display with the track_set moving for a_steps CPIs (tails > 1 point)."""
    cfg = mode_cfg(**a_fields)
    d = make_display(cfg)
    d.resize(1400, 800)
    d.show()
    rng = np.random.default_rng(SEED)
    for k in range(a_steps):
        feed_tracks(d, cfg, k, a_t=20.0 + 0.1 * k)
    d.update(*frame(cfg, 3, rng), a_t=20.0 + 0.1 * a_steps)
    d.draw_now(a_table=True)
    pump(30)
    return d, cfg


def feed_tracks(a_d, a_cfg, a_k, a_t, a_extra=(), a_drop=()):
    """track_set advanced a_k steps (range grows 2 m per step), age = a_k."""
    trks = [
        dict(t, r=t["r"] + 2.0 * a_k, age=a_k + 5)
        for t in track_set(a_cfg)
        if t["id"] not in a_drop
    ]
    a_d.update_tracks(list(a_extra) + trks, a_t)
    return trks


def label_of(a_layer, a_text):
    return next(l for l in a_layer.labels if l.isVisible() and l.toPlainText() == a_text)


def check_selected(a_d, a_id, a_tdm):
    """Ring, thick tail, yellow label on each plot of the selected track; row selected."""
    st = a_d._store.tracks[a_id]
    layers = [(a_d.det_layer, (st.v, st.r), st.tail_rv())]
    if a_tdm:
        layers.append((a_d.xy_layer, (st.x, st.y), st.tail_xy()))
    for lay, (x, y), tail in layers:
        assert lay.ring.isVisible() and (lay.ring.pos().x(), lay.ring.pos().y()) == (x, y)
        assert lay.sel_tail.xData.size == len(tail) > 1
        assert lay.sel_tail.opts["pen"].width() > lay.tails[0].opts["pen"].width()
        for lab in lay.labels:
            if lab.isVisible():
                yellow = lab.toPlainText() == f"T{st.num}"
                assert (lab.color.name() == YELLOW) == yellow, lab.toPlainText()
        assert head_colors(lay)[0][0] in ("#ff6b6b", "#4da3ff", "#7cfc9a"), "head keeps colour"
    rows = a_d.track_view.selectionModel().selectedRows()
    assert [a_d.track_model.id_at(r.row()) for r in rows] == [a_id]


def check_none_selected(a_d):
    assert a_d.selected_id() is None
    for lay in (a_d.det_layer, a_d.xy_layer):
        assert not lay.ring.isVisible() and lay.sel_tail.xData.size == 0
        assert all(l.color.name() != YELLOW for l in lay.labels if l.isVisible())
    assert not a_d.track_view.selectionModel().selectedRows()


def test_select_by_row():
    for name, fields in MODES:
        d, cfg = sel_display(fields)
        tdm = cfg.TDM_EN
        check_none_selected(d)
        row = d.track_model.row_of(1)
        assert row == 1
        d.track_view.selectRow(row)
        assert d.selected_id() == 1
        # Redraw by the timer alone: no new data, no draw_now
        wait_for(lambda: d.det_layer.ring.isVisible(), "ring drawn by the timer")
        check_selected(d, 1, tdm)
        d.track_view.selectRow(d.track_model.row_of(3))  # moves, coasting track
        wait_for(lambda: d.selected_id() == 3 and d.det_layer.ring.pos().y() == d._store.tracks[3].r,
                 "selection moved")
        d.draw_now(a_table=True)
        check_selected(d, 3, tdm)
        d.close()
    no_exceptions()


def test_select_label_past_cap():
    cfg = mode_cfg(TDM_EN=True)
    d = make_display(cfg)
    d.show()
    for k in range(3):
        d.update_tracks(
            [snap(i, 100.0 + 10 * i + k, 1.0, -30.0 + i, a_age=k, a_num=i) for i in range(40)],
            10.0 + 0.1 * k,
        )
    d.draw_now(a_table=True)
    d.track_view.selectRow(d.track_model.row_of(37))
    d.draw_now(a_table=True)
    for lay in (d.det_layer, d.xy_layer):
        assert [l.isVisible() for l in lay.labels].count(True) == 30
        assert label_of(lay, "T37").color.name() == YELLOW
    d.track_view.selectRow(0)  # label slot of T37 goes back to normal colour
    d.draw_now(a_table=True)
    assert all(l.color.name() != YELLOW or l.toPlainText() == "T0" for l in d.det_layer.labels)
    d.close()
    no_exceptions()


def head_pixel(a_plot, a_x, a_y):
    """Viewport pixel of a data point of a PlotWidget."""
    sp = a_plot.getPlotItem().vb.mapViewToScene(QPointF(a_x, a_y))
    p = a_plot.mapFromScene(sp)
    assert a_plot.viewport().rect().contains(p), (a_x, a_y, p)
    return p


def head_xy(a_d, a_id, a_kind):
    st = a_d._store.tracks[a_id]
    return (st.v, st.r) if a_kind == "rv" else (st.x, st.y)


def click(a_plot, a_pos, a_button=Qt.LeftButton):
    QTest.mouseClick(a_plot.viewport(), a_button, pos=a_pos)
    pump(20)


def test_select_by_plot_click():
    # Real QTest clicks on the viewport; they reach scene().sigMouseClicked offscreen
    for name, fields, plots in (
        ("sawtooth", {}, ("rv",)),
        ("tdm", dict(TDM_EN=True), ("rv", "xy")),
    ):
        d, cfg = sel_display(fields)
        for kind in plots:
            plot = d.det_plot if kind == "rv" else d.xy_plot
            d._clear_selection()
            for tid in (2, 0):
                pos = head_pixel(plot, *head_xy(d, tid, kind))
                click(plot, pos + QPoint(5, -4))  # within 12 px
                assert d.selected_id() == tid, (name, kind, tid)
                d.draw_now(a_table=True)
                check_selected(d, tid, cfg.TDM_EN)
                assert d.track_model.row_of(tid) == d.track_view.currentIndex().row()
            # Right click: ignored
            before = d.selected_id()
            click(plot, head_pixel(plot, *head_xy(d, 1, kind)), Qt.RightButton)
            assert d.selected_id() == before
            # A click 40 px from every head picks nothing -> clears
            pos = head_pixel(plot, *head_xy(d, before, kind))
            far = pos + QPoint(0, 40) if pos.y() < plot.viewport().height() - 80 else pos - QPoint(0, 40)
            vb = plot.getPlotItem().vb
            for t in d._store.confirmed():
                p = vb.mapViewToScene(QPointF(*head_xy(d, t.id, kind)))
                q = plot.mapToScene(far)
                assert np.hypot(p.x() - q.x(), p.y() - q.y()) > 12, "test spot must be empty"
            click(plot, far)
            assert d.selected_id() is None, (name, kind)
            d.draw_now(a_table=True)
            check_none_selected(d)
        d.close()
    no_exceptions()


def test_selection_survives_reset():
    d, cfg = sel_display(dict(TDM_EN=True))
    d.track_view.selectRow(d.track_model.row_of(2))
    state = d._sel_state
    resets = d.track_model.n_resets
    # New track with the lowest num: every row shifts by one
    new = snap(10, 250.0, 0.0, 5.0, a_age=9, a_num=-1)
    feed_tracks(d, cfg, 7, 20.7, a_extra=[new])
    d.draw_now(a_table=True)
    assert d.track_model.n_resets == resets + 1
    assert d.track_model.row_of(10) == 0 and d.track_model.row_of(2) == 3
    assert d.selected_id() == 2 and d._sel_state is state
    check_selected(d, 2, True)
    # Same cells under the highlighted row
    row = d.track_view.selectionModel().selectedRows()[0].row()
    assert d.track_model.index(row, 0).data() == "T2"
    d.close()
    no_exceptions()


def test_selection_clears():
    for how in ("gone", "restart", "config", "empty", "later_gone"):
        d, cfg = sel_display(dict(TDM_EN=True))
        d.track_view.selectRow(d.track_model.row_of(1))
        d.draw_now(a_table=True)
        assert d.selected_id() == 1
        if how == "gone":
            feed_tracks(d, cfg, 7, 20.7, a_drop=(1,))
        elif how == "restart":  # age drops: store makes a new state for the same id
            trks = [dict(t, age=0, birth_t=0.0) if t["id"] == 1 else t for t in track_set(cfg)]
            d.update_tracks(trks, 20.7)
        elif how == "config":
            d.set_config(cfg)
        elif how == "empty":
            d.update_tracks([], None)
        else:  # other tracks go, selected stays: not cleared
            feed_tracks(d, cfg, 7, 20.7, a_drop=(0, 2, 3))
            assert d.selected_id() == 1
            d.draw_now(a_table=True)
            check_selected(d, 1, True)
            d.close()
            continue
        assert d.selected_id() is None, how
        d.draw_now(a_table=True)
        check_none_selected(d)
        d.close()
    no_exceptions()


def test_track_table_model():
    from common import gui_tracks as gt

    st = gt.TrackStore(a_v_limit=20.0)
    st.update(
        [snap(0, 100.0, -2.0, 12.0, a_num=1), snap(1, 200.0, 3.0, None, a_num=0, a_coasting=True),
         snap(2, 300.0, 0.0, 0.0, a_status="tentative", a_num=None)],
        1.0,
    )
    m = gt.TrackTableModel(gui.RadarDisplay.TRACK_COLUMNS)
    changed = []
    m.dataChanged.connect(lambda *a: changed.append(a))
    assert m.refresh(st, a_now=10.0) == "reset"
    assert m.rowCount() == 2 and m.ids() == [1, 0], "confirmed only, sorted by num"
    assert [m.index(0, c).data() for c in range(6)] == ["T0", "200.0", "+3.00", "-", "20", "1.0"]
    assert m.index(1, 3).data() == "12.0"
    DR, FG = Qt.DisplayRole, Qt.ForegroundRole
    assert m.index(0, 0).data(FG) == m.GREY, "coasting row grey"
    assert m.index(1, 2).data(FG) == m.DIR_BRUSH[gt.APPROACHING], "v tinted by direction"
    assert m.index(1, 1).data(FG) is None
    assert m.headerData(2, Qt.Horizontal) == "v [m/s]"
    # Throttle: inside 200 ms nothing happens
    assert m.refresh(st, a_now=10.1) is None
    assert (m.n_resets, m.n_changes, len(changed)) == (1, 0, 0)
    # Same id list: dataChanged over the whole range, no reset
    st.update([snap(0, 101.0, -2.0, 12.0, a_num=1, a_age=6), snap(1, 201.0, 3.0, None, a_num=0,
              a_coasting=True, a_age=6)], 1.1)
    assert m.refresh(st, a_now=10.25) == "changed"
    assert (m.n_resets, m.n_changes, len(changed)) == (1, 1, 1)
    assert changed[0][0].row() == 0 and changed[0][1].row() == 1 and changed[0][1].column() == 5
    assert m.index(0, 1).data() == "201.0"
    # Different ids: reset. Force ignores the throttle
    st.update([snap(5, 50.0, 0.0, 0.0, a_num=5)], 1.2)
    assert m.refresh(st, a_force=True, a_now=10.26) == "reset"
    assert m.ids() == [5] and m.n_resets == 2
    # Wall clock path: two back to back refreshes, the second is dropped
    assert m.refresh(st, a_force=True) is not None and m.refresh(st) is None
    # Empty store: empty list, no az column without TDM
    st.clear()
    assert m.refresh(st, a_force=True) == "reset" and m.rowCount() == 0
    d = make_display(mode_cfg())
    assert d.track_view.isColumnHidden(d.TRACK_AZ_COL)
    d.set_config(mode_cfg(TDM_EN=True))
    assert not d.track_view.isColumnHidden(d.TRACK_AZ_COL)
    d.close()
    no_exceptions()


def test_table_refresh_from_draw_only():
    cfg = mode_cfg()
    d = make_display(cfg)
    d.show()
    d.update_tracks(track_set(cfg), 5.0)
    assert d.track_model.rowCount() == 0, "intake does not touch the list"
    d.draw_now(a_table=True)
    assert d.track_model.rowCount() == 4
    # Another tab: no refresh, the Tracks draw never runs
    d.centralWidget().setCurrentWidget(d._history_tab)
    d.update_tracks(track_set(cfg)[:2], 5.1)
    d.draw_now(a_table=True)
    assert d.track_model.rowCount() == 4
    d.centralWidget().setCurrentWidget(d._tracks_tab)  # tab change draws at once
    assert d.track_model.rowCount() == 2
    # Throttled refresh is made up at rest: nothing dirty, still catches up
    d.update_tracks(track_set(cfg)[:3], 5.2)
    d._draw_tracks()
    assert d.track_model.rowCount() in (2, 3)
    wait_for(lambda: d.track_model.rowCount() == 3, "throttled refresh made up")
    d.close()
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
        test_store_tail_trim,
        test_store_restart_and_clear,
        test_store_hysteresis,
        test_store_wrap_xy,
        test_trail_levels,
        test_grid_ticks,
        test_track_layer_modes_and_order,
        test_track_layer_tails_and_labels,
        test_detection_fade,
        test_adaptive_draw_interval,
        test_playback_paused_step_shows_cpi,
        test_playback,
        test_playback_tracks,
        test_select_by_row,
        test_select_label_past_cap,
        test_select_by_plot_click,
        test_selection_survives_reset,
        test_selection_clears,
        test_track_table_model,
        test_table_refresh_from_draw_only,
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
