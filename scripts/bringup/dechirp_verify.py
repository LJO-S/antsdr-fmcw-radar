#!/usr/bin/env python3
"""
Fabric dechirp / decimation verification: capture-and-compare.

Modes (digital loopback by default, --no-loopback for cable):

  default   I4: raw RX -> software frame_sync + mix_signal + process_cpi
            vs fabric NCO TX + dechirp (at --decimate 1 or 8) -> process_cpi.
  --ab      I5 step 9 (guide 7.3): fabric passthrough (/1) vs fabric /8,
            same fake targets injected on the IF stream in both phases.
            Pass = same detections in the same bins, peak-relative maps
            within --tol dB wherever either is above --floor dB, below
            --max-range.
  --sweep   enable the fabric once, sweep DECHIRP_DELAY over
            [--sweep-start, --sweep-stop), print the leakage peak per value.
  --rate S  I5 step 11 (guide 7.5), headless: fabric at --decimate, run
            read_block + process_cpi for S seconds, print CPIs per second.
            No Qt, so this is an upper bound on what the GUI reaches.

Register writes go through online/fabric_ctl.py (SshDevmem + FabricCtl), which
needs ssh KEY auth to root@<ip> (`ssh-copy-id root@192.168.5.10`, board
password "analog").

Usage (venv active):
  cd src/python
  python ../../scripts/bringup/dechirp_verify.py                 # I4, fabric /1
  python ../../scripts/bringup/dechirp_verify.py --decimate 8    # I4, fabric /8
  python ../../scripts/bringup/dechirp_verify.py --ab            # I5 step 9
  python ../../scripts/bringup/dechirp_verify.py --rate 60       # I5 step 11
  python ../../scripts/bringup/dechirp_verify.py --sweep         # delay sweep

Chirp length: every mode runs CHIRP_DUR_S snapped to the /8 sweep length
(5656 samples at 100 us / 56.6 MSPS), so software, fabric /1 and fabric /8 all
sweep the identical chirp and their range bins line up one-to-one.

Note: fabric IF output is Q15 (16-bit) but AntSDR._read_deinterleaved
normalizes by 2^11-1 (12-bit ADC). The RD maps are peak-normalized dB,
so the absolute scale cancels out in the comparison.
"""

import sys
import os
import argparse
import dataclasses
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "python"))

from common import config, dsp, fabric_regs
from online.fabric_ctl import SshDevmem, FabricCtl
from online.sdr import AntSDR
from online.target_sim import TargetSim

# Digital-loopback TX->RX latency (measured 2026-09-10, CLAUDE.md). The config
# default (61) is the cable value.
LOOPBACK_DECHIRP_DELAY = 34


def _print_fabric_config(image, delay):
    """One-line summary of what ctl.enable() just wrote, pulled from the same
    register image (never recomputed)."""
    # The image CTRL is the pre-enable word; enable() ORs in nco_en for the
    # post-COMMIT write, so report what actually ends up in the register.
    ctrl = image[fabric_regs.REG_CTRL] | fabric_regs.CTRL_NCO_EN
    print(
        f"  Fabric configured: delay={delay}, ctrl=0x{ctrl:02X}, "
        f"sweep_len={image[fabric_regs.REG_SWEEP_LEN]}, "
        f"decim_sel={image.get(fabric_regs.REG_DECIM_SEL, 0)}"
    )


def _print_dets(dets):
    for d in dets:
        print(f"    det: r={d['r']:.1f} m  v={d['v']:.2f} m/s  kind={d['kind']}")


class FrozenTargetSim(TargetSim):
    """Range pinned at r0, velocity v0 (Doppler only). TargetSim moves targets
    by wall-clock time, and apply_if builds one echo after the other: at /1
    each echo costs ~8x longer, so later targets drift ~0.1 range bin further
    than at /8 and the A/B reads that drift as a map difference."""

    def _kinematics(self, a_target, a_config):
        return a_target.r0, a_target.v0


def parse_targets(a_spec, a_amp):
    """'100:20,300:-20' -> FakeTarget dicts (range m : velocity m/s)."""
    targets = []
    for item in a_spec.split(","):
        r, v = item.split(":")
        targets.append(
            dict(r0=float(r), v0=float(v), a0=0.0, amp=a_amp, duration=1e6)
        )
    return targets


# ---------------------------------------------------------------------------
# Capture + process
# ---------------------------------------------------------------------------
def capture_sw(radio, cfg, ctx, n_captures):
    """Software phase: raw RX -> software frame_sync + mix_signal + process_cpi."""
    for _ in range(3):
        radio.read_block()

    rd_maps = []
    raw_blocks = []
    chirp_offset = None

    for i in range(n_captures):
        rx = radio.read_block()
        raw_blocks.append(rx)

        m = dsp.estimate_chirp_offset(a_rx=rx, a_ref_period=ctx.tx_chirp)
        if chirp_offset is None:
            chirp_offset = m
            print(f"  chirp offset = {m} samples")

        rx_aligned = dsp.frame_sync_linear(a_rx=rx, a_config=cfg, a_ctx=ctx)
        if_signal = dsp.mix_signal(a_rx_signal=rx_aligned, a_tx_signal=ctx.tx_seq)
        rd_up, rd_down, dets, ranges, velocities = dsp.process_cpi(
            a_if_signal=if_signal, a_config=cfg, a_ctx=ctx
        )
        rd_maps.append(rd_up)

        if i == 0:
            _print_dets(dets)

    rd_avg = np.mean(rd_maps, axis=0)
    return rd_avg, chirp_offset, ranges, velocities, raw_blocks


def fabric_if(rx, cfg):
    """Cut one CPI of fabric IF out of a DMA block, same slice as capture.py."""
    rows = 2 * cfg.CHIRP_REPS if cfg.TRIANGLE_EN else cfg.CHIRP_REPS
    trim = cfg.FABRIC_FRAME_TRIM
    return rx[trim : trim + rows * cfg.N_IF]


def capture_fabric(radio, cfg, ctx, n_captures, a_sim=None, a_targets=None):
    """Fabric phase: IF (already dechirped, frame-aligned, maybe decimated) ->
    optional fake targets -> process_cpi. Returns the averaged up-map, the
    first capture's detections, the axes, the raw blocks and one clean
    (pre-injection) IF CPI for the leakage profile."""
    for _ in range(8):
        radio.read_block()

    rd_maps = []
    raw_blocks = []
    dets0 = None
    clean_if = None

    for i in range(n_captures):
        rx = radio.read_block()
        raw_blocks.append(rx)

        if_signal = fabric_if(rx, cfg)
        if clean_if is None:
            clean_if = if_signal.copy()
        if a_sim is not None:
            if_signal = a_sim.apply_if(a_if_raw=if_signal, a_config=cfg, a_ctx=ctx)

        rd_up, rd_down, dets, ranges, velocities = dsp.process_cpi(
            a_if_signal=if_signal, a_config=cfg, a_ctx=ctx
        )
        rd_maps.append(rd_up)

        if i == 0:
            dets0 = dets
            _print_dets(dets)

    rd_avg = np.mean(rd_maps, axis=0)
    return rd_avg, dets0, ranges, velocities, raw_blocks, clean_if


def fabric_phase(radio, ctl, pcfg, loopback, n_captures, a_sim=None, a_targets=None):
    """Restart the radio at pcfg (buffer sized from its N_IF), enable the
    fabric, capture, disable. The app's order: radio.start() -> fabric up,
    fabric down -> radio.close()."""
    radio.close()
    radio.config = pcfg
    radio.set_loopback(loopback)
    radio.start()
    ctx = dsp.build_cpi_context(a_config=pcfg)
    if a_sim is not None:
        a_sim.set_targets(a_targets)
    image = fabric_regs.register_image(pcfg)
    try:
        ctl.enable(image)
        _print_fabric_config(image, pcfg.FABRIC_DECHIRP_DELAY)
        return capture_fabric(radio, pcfg, ctx, n_captures, a_sim, a_targets), ctx
    finally:
        ctl.disable()


def range_profile_db(if_signal, ctx):
    """One chirp's worth of already-dechirped IF -> (mag_db over ctx.ranges_pos, peak_bin,
    peak_range_m, peak_mag_db). No CFAR/close-in masking (unlike process_cpi, which
    overwrites the first CFAR_MASK_N bins - exactly the leakage we're trying to see here).
    Un-normalized (not peak-relative like the RD map convention) so magnitude is directly
    comparable across delay steps, not just within one frame."""
    n = ctx.N_if_samples
    chirp = if_signal[:n]
    windowed = chirp * np.blackman(n)  # matches process_cpi's range_window
    spectrum = np.fft.fft(windowed)
    mag_db = 20 * np.log10(np.abs(spectrum) + 1e-12)
    mag_db_pos = mag_db[ctx.pos]  # same positive-range mask as process_cpi
    peak_bin = int(np.argmax(mag_db_pos))
    return mag_db_pos, peak_bin, ctx.ranges_pos[peak_bin], mag_db_pos[peak_bin]


def run_sweep(radio, ctl, cfg, ctx, delays, n_captures):
    """Sweep DECHIRP_DELAY over `delays` with the fabric enabled exactly once (by the
    caller, before this runs). Prints one row per delay - no CTRL writes happen in here,
    only ctl.set_calib_delay() (DECHIRP_DELAY + COMMIT).
    """
    print(f"\n{'delay':>6} {'peak_bin':>9} {'range_m':>9} {'peak_dB':>9}")
    rows = []
    for delay in delays:
        ctl.set_calib_delay(delay)
        radio.read_block()  # discard one stale/in-flight capture (cheap insurance)

        profiles = []
        for _ in range(n_captures):
            rx = radio.read_block()
            mag_db_pos, _, _, _ = range_profile_db(fabric_if(rx, cfg), ctx)
            profiles.append(mag_db_pos)
        mag_db_avg = np.mean(profiles, axis=0)
        peak_bin = int(np.argmax(mag_db_avg))
        row = (
            delay,
            peak_bin,
            float(ctx.ranges_pos[peak_bin]),
            float(mag_db_avg[peak_bin]),
        )
        rows.append(row)
        print(f"{row[0]:6d} {row[1]:9d} {row[2]:9.2f} {row[3]:9.2f}")

    return rows


def run_rate(radio, cfg, ctx, seconds):
    """Headless CPIs per second: read_block + slice + process_cpi, split into
    capture time and processing time so it is clear which one binds."""
    for _ in range(3):
        radio.read_block()
    n = 0
    t_cap = t_proc = 0.0
    t_end = time.monotonic() + seconds
    t0 = time.monotonic()
    while time.monotonic() < t_end:
        a = time.perf_counter()
        rx = radio.read_block()
        b = time.perf_counter()
        dsp.process_cpi(a_if_signal=fabric_if(rx, cfg), a_config=cfg, a_ctx=ctx)
        c = time.perf_counter()
        t_cap += b - a
        t_proc += c - b
        n += 1
    wall = time.monotonic() - t0
    print(f"\n--- Rate ({seconds:.0f} s) ---")
    print(f"  CPIs:            {n}")
    print(f"  CPIs per second: {n / wall:.2f}")
    print(f"  read_block:      {1e3 * t_cap / n:.1f} ms/CPI")
    print(f"  process_cpi:     {1e3 * t_proc / n:.1f} ms/CPI")


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def _crop(rd_a, rd_b, ranges_a, ranges_b, max_range):
    """Common range bins below max_range. Both maps are (velocity, range)."""
    n = min(len(ranges_a), len(ranges_b))
    n = int(np.count_nonzero(ranges_a[:n] <= max_range))
    return rd_a[:, :n], rd_b[:, :n], ranges_a[:n]


def compare(
    rd_a,
    rd_b,
    ranges_a,
    ranges_b,
    velocities,
    labels,
    floor_db,
    tol_db,
    max_range,
    title,
    save_plot,
    noise_margin_db=None,
):
    """Peak-relative RD maps: max |diff| over cells where either map is above
    the floor, plus peak-bin match. Pass = both.

    The floor is floor_db, raised to (noise median + noise_margin_db) when a
    margin is given: noise cells are independent draws in the two phases and
    differ by several dB by construction, so only signal cells can be graded."""
    a, b, ranges = _crop(rd_a, rd_b, ranges_a, ranges_b, max_range)
    if noise_margin_db is not None:
        noise_db = max(float(np.median(a)), float(np.median(b)))
        if noise_db + noise_margin_db > floor_db:
            print(
                f"  Floor raised {floor_db:.0f} -> {noise_db + noise_margin_db:.1f} dB "
                f"(noise median {noise_db:.1f} + {noise_margin_db:.0f})"
            )
            floor_db = noise_db + noise_margin_db
    step = abs(ranges_a[1] - ranges_a[0]) - abs(ranges_b[1] - ranges_b[0])
    if abs(step) > 1e-6:
        print(f"  WARNING: range bin widths differ by {step:.6f} m")

    diff = b - a
    mask = np.maximum(a, b) > floor_db
    max_abs = float(np.max(np.abs(diff[mask]))) if mask.any() else 0.0
    rms = float(np.sqrt(np.mean(diff[mask] ** 2))) if mask.any() else 0.0

    peak_a = np.unravel_index(np.argmax(a), a.shape)
    peak_b = np.unravel_index(np.argmax(b), b.shape)
    peak_match = peak_a == peak_b

    print(f"\n--- Map comparison ({labels[0]} vs {labels[1]}) ---")
    print(f"  Range bins:   {a.shape[1]} (to {ranges[-1]:.0f} m)")
    print(f"  Cells > {floor_db:.0f} dB: {int(mask.sum())}")
    print(f"  Max |diff|:   {max_abs:.2f} dB (tolerance {tol_db:.1f})")
    print(f"  RMS diff:     {rms:.2f} dB")
    print(f"  {labels[0]} peak bin: {peak_a}")
    print(f"  {labels[1]} peak bin: {peak_b}")
    print(f"  Peak match:   {'YES' if peak_match else 'NO'}")

    passed = max_abs < tol_db and peak_match
    print(f"  {'PASS' if passed else 'FAIL'}")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not available, skipping plot)")
        return passed

    extent = [velocities[0], velocities[-1], ranges[0], ranges[-1]]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    for ax, data, name in [(axes[0], a, labels[0]), (axes[1], b, labels[1])]:
        ax.imshow(
            data.T,
            aspect="auto",
            origin="lower",
            extent=extent,
            vmin=-80,
            vmax=0,
            cmap="viridis",
        )
        ax.set_title(name)
        ax.set_xlabel("Velocity [m/s]")
        ax.set_ylabel("Range [m]")

    shown = np.where(mask, diff, np.nan)
    im = axes[2].imshow(
        shown.T, aspect="auto", origin="lower", extent=extent, cmap="RdBu_r"
    )
    axes[2].set_title(f"Diff where > {floor_db:.0f} dB (max {max_abs:.1f} dB)")
    axes[2].set_xlabel("Velocity [m/s]")
    axes[2].set_ylabel("Range [m]")
    fig.colorbar(im, ax=axes[2], label="dB")

    fig.suptitle(f"{title} -- {'PASS' if passed else 'FAIL'}")
    plt.tight_layout()

    if save_plot:
        plt.savefig(save_plot, dpi=150)
        print(f"  Plot saved: {save_plot}")
    plt.show()

    return passed


def compare_dets(dets_a, dets_b, targets, ranges, velocities, labels, max_range):
    """Every injected target below max_range found in both phases, and the two
    detection lists agree within 1.5 bins in range and velocity."""
    tol_r = 1.5 * abs(ranges[1] - ranges[0])
    tol_v = 1.5 * abs(velocities[1] - velocities[0])

    def near(d, r, v):
        return abs(d["r"] - r) <= tol_r and abs(d["v"] - v) <= tol_v

    print(f"\n--- Detections (tolerance {tol_r:.1f} m, {tol_v:.2f} m/s) ---")
    ok = True
    for t in targets:
        if t["r0"] > max_range:
            continue
        hit = [any(near(d, t["r0"], t["v0"]) for d in ds) for ds in (dets_a, dets_b)]
        print(
            f"  target {t['r0']:6.1f} m {t['v0']:+6.1f} m/s: "
            f"{labels[0]} {'found' if hit[0] else 'MISSING'}, "
            f"{labels[1]} {'found' if hit[1] else 'MISSING'}"
        )
        ok &= all(hit)

    for src, other, name in [
        (dets_a, dets_b, labels[0]),
        (dets_b, dets_a, labels[1]),
    ]:
        for d in src:
            if d["r"] > max_range:
                continue
            if not any(near(o, d["r"], d["v"]) for o in other):
                print(f"  only in {name}: r={d['r']:.1f} m v={d['v']:.2f} m/s")
                ok = False

    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def compare_leakage(clean_a, clean_b, ctx_a, ctx_b, labels, n_bins=8):
    """Unmasked first-chirp range profile, peak-relative, first n_bins: the
    leakage line that process_cpi's close-in mask hides in the RD map."""
    pa = range_profile_db(clean_a, ctx_a)[0][:n_bins]
    pb = range_profile_db(clean_b, ctx_b)[0][:n_bins]
    pa -= pa.max()
    pb -= pb.max()
    print(f"\n--- Leakage line, first {n_bins} range bins (peak-relative dB) ---")
    print(f"  {labels[0]:>10}: " + " ".join(f"{x:6.1f}" for x in pa))
    print(f"  {labels[1]:>10}: " + " ".join(f"{x:6.1f}" for x in pb))
    print(f"  max |diff| {np.max(np.abs(pa - pb)):.2f} dB (informational)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Fabric dechirp/decimation verification")
    ap.add_argument("--ip", default="192.168.5.10")
    ap.add_argument(
        "--delay",
        type=int,
        default=None,
        help=f"dechirp delay in samples (default: {LOOPBACK_DECHIRP_DELAY} in "
        "loopback, config FABRIC_DECHIRP_DELAY with --no-loopback, "
        "--sweep-start in sweep mode)",
    )
    ap.add_argument(
        "--decimate",
        type=int,
        choices=(1, 8),
        default=1,
        help="fabric ratio for the default, --sweep and --rate modes",
    )
    ap.add_argument(
        "--trim",
        type=int,
        default=None,
        help="FABRIC_FRAME_TRIM override (default: config)",
    )
    ap.add_argument(
        "--captures",
        type=int,
        default=5,
        help="CPIs to average per phase (also controls sweep averaging - "
        "lower it, e.g. 1-2, to make a full --sweep run faster)",
    )
    ap.add_argument(
        "--save", action="store_true", help="save raw captures to dechirp_verify.npz"
    )
    ap.add_argument(
        "--no-loopback",
        dest="loopback",
        action="store_false",
        default=True,
        help="disable digital loopback",
    )
    ap.add_argument(
        "--plot",
        default="dechirp_verify.png",
        help="plot output path (empty to skip save)",
    )
    # --ab
    ap.add_argument("--ab", action="store_true", help="fabric /1 vs /8 (I5 step 9)")
    ap.add_argument(
        "--targets",
        default="100:20,300:-20,500:20",
        help="fake targets for --ab, 'range_m:vel_mps,...'",
    )
    ap.add_argument(
        "--amp",
        type=float,
        default=0.1,
        help="fake target amplitude relative to the IF block RMS",
    )
    ap.add_argument(
        "--no-noise",
        dest="noise",
        action="store_false",
        default=True,
        help="--ab: no injected loopback noise",
    )
    ap.add_argument("--floor", type=float, default=-60.0, help="--ab: map floor dB")
    ap.add_argument("--tol", type=float, default=1.0, help="--ab: map tolerance dB")
    ap.add_argument(
        "--noise-margin",
        type=float,
        default=25.0,
        help="--ab: grade only cells this far above the noise median [dB] "
        "(at 15 dB, noise alone swings a cell ~1.5 dB)",
    )
    ap.add_argument(
        "--max-range",
        type=float,
        default=750.0,
        help="--ab: compare below this range [m]",
    )
    # --rate
    ap.add_argument(
        "--rate",
        type=float,
        default=None,
        metavar="SECONDS",
        help="measure CPIs per second at --decimate for SECONDS (I5 step 11)",
    )
    # --sweep
    ap.add_argument(
        "--sweep",
        action="store_true",
        help="enable the fabric once, then sweep DECHIRP_DELAY over "
        "[--sweep-start, --sweep-stop) printing the leakage peak "
        "range per value, instead of the single-delay compare",
    )
    ap.add_argument("--sweep-start", type=int, default=0)
    ap.add_argument("--sweep-stop", type=int, default=128)
    ap.add_argument("--sweep-step", type=int, default=1)
    args = ap.parse_args()

    if args.delay is not None:
        delay = args.delay
    elif args.sweep:
        delay = args.sweep_start
    elif args.loopback:
        delay = LOOPBACK_DECHIRP_DELAY
    else:
        delay = config.RadarConfig().FABRIC_DECHIRP_DELAY

    base = config.RadarConfig()
    # Snap the chirp to the /8 sweep length (see module docstring).
    sweep8 = dataclasses.replace(
        base, FABRIC_DECHIRP_EN=True, FABRIC_DECIM_EN=True
    ).SWEEP_LEN
    trim = base.FABRIC_FRAME_TRIM if args.trim is None else args.trim
    sw_cfg = dataclasses.replace(
        base,
        CHIRP_DUR_S=sweep8 / base.FS,
        FABRIC_DECHIRP_EN=False,
        FABRIC_DECHIRP_DELAY=delay,
        FABRIC_FRAME_TRIM=trim,
        SDR_LOOPBACK_EN=args.loopback,
    )

    def fab_cfg(a_ratio):
        pcfg = dataclasses.replace(
            sw_cfg, FABRIC_DECHIRP_EN=True, FABRIC_DECIM_EN=(a_ratio == 8)
        )
        assert pcfg.SWEEP_LEN == sweep8, (pcfg.SWEEP_LEN, sweep8)
        return pcfg

    ctl = FabricCtl(SshDevmem(args.ip))

    mode = "A/B /1 vs /8" if args.ab else "sweep" if args.sweep else "rate" if (
        args.rate is not None
    ) else f"software vs fabric /{args.decimate}"
    print("Fabric dechirp verification")
    print(f"  Mode:     {mode}")
    print(f"  IP:       {args.ip}")
    print(f"  FS:       {sw_cfg.FS / 1e6:.1f} MHz")
    print(f"  BW:       {sw_cfg.CHIRP_BW_HZ / 1e6:.1f} MHz")
    print(f"  T:        {sw_cfg.CHIRP_DUR_S * 1e6:.3f} us ({sweep8} samples)")
    print(f"  Chirps:   {sw_cfg.CHIRP_REPS}")
    print(f"  Triangle: {sw_cfg.TRIANGLE_EN}")
    print(f"  Delay:    {delay}")
    print(f"  Trim:     {trim}")

    print("\nChecking firmware...")
    ctl.check_magic()
    print("  MAGIC OK")

    print("\nConnecting SDR...")
    first_cfg = sw_cfg if not (args.ab or args.rate is not None or args.sweep) else (
        fab_cfg(1 if args.ab else args.decimate)
    )
    radio = AntSDR(a_radar_config=first_cfg)
    radio.set_loopback(args.loopback)

    try:
        radio.start()

        # ------------------------------------------------------------ A/B
        if args.ab:
            targets = parse_targets(args.targets, args.amp)
            cfg1 = fab_cfg(1)
            cfg8 = fab_cfg(8)
            snr = cfg1.SDR_LOOPBACK_NOISE_SNR_DB
            if not args.noise:
                snr = 0.0
            # apply_noise sets noise relative to the block RMS per IF sample.
            # Real decimation removes 7/8 of white noise, so /8 gets the same
            # noise per range bin only at +10*log10(8) dB of per-sample SNR.
            cfg1 = dataclasses.replace(cfg1, SDR_LOOPBACK_NOISE_SNR_DB=snr)
            cfg8 = dataclasses.replace(
                cfg8,
                SDR_LOOPBACK_NOISE_SNR_DB=(snr + 10 * np.log10(8)) if snr > 0 else 0.0,
            )
            print(f"  Targets:  {args.targets} (amp {args.amp})")
            print(
                f"  Noise:    {cfg1.SDR_LOOPBACK_NOISE_SNR_DB:.1f} dB at /1, "
                f"{cfg8.SDR_LOOPBACK_NOISE_SNR_DB:.1f} dB at /8"
                if snr > 0
                else "  Noise:    off"
            )
            print(f"  Effective range at /8: {cfg8.EFFECTIVE_RANGE:.0f} m")

            sim = FrozenTargetSim()
            print("\n--- Phase A: fabric /1 (passthrough) ---")
            (rd1, dets1, r1, v1, raw1, clean1), ctx1 = fabric_phase(
                radio, ctl, cfg1, args.loopback, args.captures, sim, targets
            )
            print("\n--- Phase B: fabric /8 ---")
            (rd8, dets8, r8, v8, raw8, clean8), ctx8 = fabric_phase(
                radio, ctl, cfg8, args.loopback, args.captures, sim, targets
            )

            if args.save:
                np.savez(
                    "dechirp_verify_ab.npz",
                    rd1=rd1,
                    rd8=rd8,
                    ranges1=r1,
                    ranges8=r8,
                    velocities=v1,
                    raw1_0=raw1[0],
                    raw8_0=raw8[0],
                )
                print("  Saved: dechirp_verify_ab.npz")

            labels = ("/1", "/8")
            compare_leakage(clean1, clean8, ctx1, ctx8, labels)
            ok_det = compare_dets(dets1, dets8, targets, r1, v1, labels, args.max_range)
            ok_map = compare(
                rd1,
                rd8,
                r1,
                r8,
                v1,
                labels,
                args.floor,
                args.tol,
                args.max_range,
                "I5 decimation A/B",
                args.plot if args.plot else None,
                args.noise_margin,
            )
            passed = ok_det and ok_map
            print(f"\n{'PASS' if passed else 'FAIL'}: decimation A/B")
            sys.exit(0 if passed else 1)

        # ----------------------------------------------------------- rate
        if args.rate is not None:
            pcfg = fab_cfg(args.decimate)
            ctx = dsp.build_cpi_context(a_config=pcfg)
            image = fabric_regs.register_image(pcfg)
            ctl.enable(image)
            _print_fabric_config(image, delay)
            run_rate(radio, pcfg, ctx, args.rate)
            return

        # ---------------------------------------------------------- sweep
        if args.sweep:
            # Fabric is enabled ONCE here, with `delay` as the starting value.
            # The sweep loop below only ever calls ctl.set_calib_delay() -
            # CTRL/nco_en/tx_src/sync_src are never touched again.
            pcfg = fab_cfg(args.decimate)
            ctx = dsp.build_cpi_context(a_config=pcfg)
            image = fabric_regs.register_image(pcfg)
            print("\n--- Sweep: fabric dechirp delay ---")
            ctl.enable(image)
            _print_fabric_config(image, delay)
            run_sweep(
                radio,
                ctl,
                pcfg,
                ctx,
                range(args.sweep_start, args.sweep_stop, args.sweep_step),
                args.captures,
            )
            return

        # --------------------------------------------- software vs fabric
        print("\n--- Phase A: software dechirp ---")
        sw_ctx = dsp.build_cpi_context(a_config=sw_cfg)
        rd_sw, measured_delay, ranges_sw, velocities, raw_sw = capture_sw(
            radio, sw_cfg, sw_ctx, args.captures
        )
        # estimate_chirp_offset is a frame-sync offset into the RX block, not a
        # TX->RX dechirp latency - printed for reference only.
        print(f"\n  (Phase A chirp offset was {measured_delay})")

        print(f"\n--- Phase B: fabric dechirp /{args.decimate} ---")
        (rd_fb, _, ranges_fb, _, raw_fb, _), _ = fabric_phase(
            radio, ctl, fab_cfg(args.decimate), args.loopback, args.captures
        )

        if args.save:
            np.savez(
                "dechirp_verify.npz",
                rd_sw=rd_sw,
                rd_fb=rd_fb,
                ranges_sw=ranges_sw,
                ranges_fb=ranges_fb,
                velocities=velocities,
                delay=delay,
                raw_sw_0=raw_sw[0],
                raw_fb_0=raw_fb[0],
            )
            print("  Saved: dechirp_verify.npz")

        passed = compare(
            rd_sw,
            rd_fb,
            ranges_sw,
            ranges_fb,
            velocities,
            ("Software", f"Fabric /{args.decimate}"),
            -80.0,
            6.0,
            sw_cfg.MAX_RANGE,
            "Dechirp verification",
            args.plot if args.plot else None,
        )
        sys.exit(0 if passed else 1)

    finally:
        try:
            ctl.disable()
        except Exception as e:
            print(f"  WARNING: fabric cleanup failed: {e}")
        radio.set_loopback(False)
        radio.close()


if __name__ == "__main__":
    main()
