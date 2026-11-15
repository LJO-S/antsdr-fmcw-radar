"""
L4 GUI profiler: median ms per CPI of RadarDisplay.update() (intake) and of
draw_now() + QApplication.processEvents() after it (draw and paint), synthetic frames.
Timer not involved: each frame taken in, then drawn once.

    python -m offline.profile_gui [--screen] [--frames N] [--tracks M]
    python -m offline.profile_gui --mode TDM --tab Tracks   (one cell, JSON)

Per mode (sawtooth, TDM, triangle + TDM), detection count (0 / 20 / 100) and shown
tab (Tracks, History, Signal; Config draws nothing per frame).
Signal tab: intake also covers update_signals(), fed each frame with spectrogram
shaped like processing.spectrograms fabric output. Offscreen unless --screen.
Config: fabric /8 (FABRIC_DECHIRP_EN) = 128 x 707 map.
CPU boost ramps under sustained load; without warm-up spin first cells read ~1.6x slow.
Numbers compare only on same machine, on AC power.
Each (mode, tab) cell runs in own process, no inherited state.
Mirrors live per-CPI cost; no board, no worker thread.
"""

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np

N_DOPPLER, N_RANGE = 128, 707
DETECTIONS = (0, 20, 100)
POOL = 8  # distinct frames cycled, no CPI repeats predecessor
WARMUP = 10
TABS = ["Tracks", "History", "Signal"]
MODES = [
    ("Sawtooth", dict()),
    ("TDM", dict(TDM_EN=True)),
    ("Triangle + TDM", dict(TRIANGLE_EN=True, TDM_EN=True)),
]


def make_frames(a_cfg, a_n_det, a_rng):
    ranges = np.linspace(0, a_cfg.MAX_RANGE, N_RANGE)
    vels = np.linspace(-a_cfg.MAX_VELOCITY, a_cfg.MAX_VELOCITY, N_DOPPLER)
    kinds = ["both", "up", "down"] if a_cfg.TRIANGLE_EN else ["up"]
    frames = []
    for _ in range(POOL):
        maps = []
        for _ in range(2 if a_cfg.TRIANGLE_EN else 1):
            rd = a_rng.normal(-60, 4, (N_DOPPLER, N_RANGE)).astype(np.float32)
            for _ in range(5):
                rd[a_rng.integers(2, N_DOPPLER - 2), a_rng.integers(2, N_RANGE - 2)] = -5
            maps.append(rd)
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
        frames.append((maps[0], maps[1] if len(maps) > 1 else None, ranges, vels, targets))
    return frames


def make_specs(a_cfg, a_rng):
    """POOL (spec, t, f) like processing.spectrograms returns in fabric mode: 2
    period slice of IF stream through dsp.spectrogram."""
    from common import dsp

    n = 2 * a_cfg.LEGS_PER_PERIOD * a_cfg.N_IF
    specs = []
    for _ in range(POOL):
        x = (a_rng.standard_normal(n) + 1j * a_rng.standard_normal(n)).astype(
            np.complex64
        )
        specs.append(dsp.spectrogram(a_signal=x, a_config=a_cfg, a_fs=a_cfg.FS_IF))
    return specs


def measure(a_app, a_display, a_frames, a_n, a_specs=None):
    """Median ms of update() (+ update_signals() when a_specs) and of draw_now() +
    following processEvents()."""
    intake, paint = [], []
    for k in range(WARMUP + a_n):
        up, down, ranges, vels, targets = a_frames[k % len(a_frames)]
        t0 = time.perf_counter()
        a_display.update(up, down, ranges, vels, targets)
        if a_specs:
            spec, t, f = a_specs[k % len(a_specs)]
            a_display.update_signals(spec, None, t, f)
        t1 = time.perf_counter()
        a_display.draw_now()
        a_app.processEvents()
        t2 = time.perf_counter()
        if k >= WARMUP:
            intake.append(t1 - t0)
            paint.append(t2 - t1)
    return 1e3 * np.median(intake), 1e3 * np.median(paint)


def warm_up(a_seconds):
    """Busy-spin so CPU is at sustained clock before first cell."""
    end = time.perf_counter() + a_seconds
    x = np.random.default_rng(0).standard_normal(200_000)
    while time.perf_counter() < end:
        np.sort(x)


def run_cell(a_args):
    """One (mode, tab) cell in this process: list of (intake, paint) ms per
    detection count."""
    if not a_args.screen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # before any Qt import

    from PySide6.QtWidgets import QApplication, QTabWidget

    from common import config, gui

    app = QApplication(sys.argv)
    rng = np.random.default_rng(0)
    fields = dict(MODES)[a_args.mode]
    cfg = config.RadarConfig(FABRIC_DECHIRP_EN=True, SDR_LOOPBACK_EN=False, **fields)
    assert cfg.N_IF == N_RANGE and cfg.CHIRP_REPS == N_DOPPLER, "not a 128 x 707 map"
    display = gui.RadarDisplay(a_config=cfg)
    display.resize(1600, 900)
    display.show()
    tabs = display.centralWidget()
    assert isinstance(tabs, QTabWidget)
    tabs.setCurrentWidget(
        {"Tracks": display._tracks_tab, "History": display._history_tab,
         "Signal": display._signal_tab}[a_args.tab]
    )
    app.processEvents()
    row = []
    for n_det in DETECTIONS:
        display.set_config(cfg)  # fresh History per cell
        frames = make_frames(cfg, n_det, rng)
        specs = make_specs(cfg, rng) if a_args.tab == "Signal" else None
        row.append(measure(app, display, frames, a_args.frames, specs))
    display.close()
    return row


def main():
    ap = argparse.ArgumentParser(description="Profile RadarDisplay per CPI")
    ap.add_argument("--screen", action="store_true", help="real platform, not offscreen")
    ap.add_argument("--frames", type=int, default=100, help="measured frames per cell")
    ap.add_argument("--tracks", type=int, default=0, help="tracks per frame (no-op until WP5)")
    ap.add_argument("--warmup-s", type=float, default=8.0, help="CPU spin before measuring (0 = off)")
    ap.add_argument("--mode", choices=[m for m, _ in MODES], help="one cell, in-process")
    ap.add_argument("--tab", choices=TABS, help="one cell, in-process")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.mode or args.tab:
        # one cell by hand, or child of full run: JSON on last line
        args.mode = args.mode or MODES[0][0]
        args.tab = args.tab or TABS[0]
        if not args.child:
            warm_up(args.warmup_s)
        print(json.dumps(run_cell(args)))
        return

    # one spin, then cells back to back (fresh process each) keep boost
    warm_up(args.warmup_s)
    results = {}
    for tab in TABS:
        for mode, _ in MODES:
            cmd = [sys.executable, "-m", "offline.profile_gui", "--mode", mode,
                   "--tab", tab, "--frames", str(args.frames),
                   "--tracks", str(args.tracks), "--child"]
            if args.screen:
                cmd.append("--screen")
            out = subprocess.run(cmd, capture_output=True, text=True, check=True)
            results[(tab, mode)] = json.loads(out.stdout.strip().splitlines()[-1])

    for tab in TABS:
        print(f"\n{tab} tab, {N_DOPPLER} x {N_RANGE} map, 1600x900, "
              f"{args.frames} frames/cell, median per CPI (draw = draw_now + paint)")
        print("| Mode | Detections | intake | draw |")
        print("|---|---|---|---|")
        for mode, _ in MODES:
            row = results[(tab, mode)]
            det = " / ".join(str(n) for n in DETECTIONS)
            upd = " / ".join(f"{r[0]:.1f}" for r in row)
            pnt = " / ".join(f"{r[1]:.1f}" for r in row)
            print(f"| {mode} | {det} | {upd} ms | {pnt} ms |")


if __name__ == "__main__":
    main()
