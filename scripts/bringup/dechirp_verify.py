#!/usr/bin/env python3
"""
I4 fabric dechirp verification: capture-and-compare.

Two-phase test on digital loopback (or cable):
  Phase A  raw RX -> software frame_sync + mix_signal + process_cpi
  Phase B  fabric NCO TX + dechirp -> process_cpi (no mix_signal)

Compares the two RD maps.  Register writes go through online/fabric_ctl.py
(SshDevmem + FabricCtl), which needs ssh KEY auth to root@<ip>
(`ssh-copy-id root@192.168.5.10`, board password "analog") - not sshpass.

Usage (from repo root, venv active):
  cd src/python
  python ../../scripts/bringup/dechirp_verify.py [--delay D] [--ip IP]

  --delay D   fabric dechirp delay in samples (default: the config's
              FABRIC_DECHIRP_DELAY, or --sweep-start in sweep mode)
  --captures  number of CPIs to average per phase (default 5)
  --save      save raw captures to .npz for offline re-analysis
  --sweep     enable the fabric once, then sweep DECHIRP_DELAY over
              [--sweep-start, --sweep-stop) printing the leakage peak's
              range per value (no CTRL writes during the sweep itself -
              use this instead of re-running the script per --delay value)

Note: fabric IF output is Q15 (16-bit) but AntSDR._read_deinterleaved
normalizes by 2^11-1 (12-bit ADC). The RD maps are peak-normalized dB,
so the absolute scale cancels out in the comparison.
"""

import sys
import os
import argparse
import dataclasses

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "python"))

from common import config, dsp, fabric_regs
from online.fabric_ctl import SshDevmem, FabricCtl
from online.sdr import AntSDR


def _print_fabric_config(image, delay):
    """One-line summary of what ctl.enable() just wrote, pulled from the same
    register image (never recomputed)."""
    # The image CTRL is the pre-enable word; enable() ORs in nco_en for the
    # post-COMMIT write, so report what actually ends up in the register.
    ctrl = image[fabric_regs.REG_CTRL] | fabric_regs.CTRL_NCO_EN
    print(
        f"  Fabric configured: delay={delay}, ctrl=0x{ctrl:02X}, "
        f"sweep_len={image[fabric_regs.REG_SWEEP_LEN]}"
    )


# ---------------------------------------------------------------------------
# Capture + process
# ---------------------------------------------------------------------------
def capture_sw(radio, cfg, ctx, n_captures):
    """Phase A: raw RX -> software frame_sync + mix_signal + process_cpi."""
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
            for d in dets:
                print(
                    f"    det: r={d['r']:.1f} m  v={d['v']:.2f} m/s  kind={d['kind']}"
                )

    rd_avg = np.mean(rd_maps, axis=0)
    return rd_avg, chirp_offset, ranges, velocities, raw_blocks


def capture_fabric(radio, cfg, ctx, n_captures):
    """Phase B: fabric IF (already dechirped, frame-aligned) -> process_cpi."""
    P = ctx.N_chirp_samples
    n_cpi = cfg.CHIRP_REPS * P
    if cfg.TRIANGLE_EN:
        n_cpi = 2 * cfg.CHIRP_REPS * P

    for _ in range(8):
        radio.read_block()

    rd_maps = []
    raw_blocks = []

    for i in range(n_captures):
        rx = radio.read_block()
        raw_blocks.append(rx)

        if_signal = rx[:n_cpi]
        rd_up, rd_down, dets, ranges, velocities = dsp.process_cpi(
            a_if_signal=if_signal, a_config=cfg, a_ctx=ctx
        )
        rd_maps.append(rd_up)

        if i == 0:
            for d in dets:
                print(
                    f"    det: r={d['r']:.1f} m  v={d['v']:.2f} m/s  kind={d['kind']}"
                )

    rd_avg = np.mean(rd_maps, axis=0)
    return rd_avg, ranges, velocities, raw_blocks


def range_profile_db(if_signal, ctx):
    """One chirp's worth of already-dechirped IF -> (mag_db over ctx.ranges_pos, peak_bin,
    peak_range_m, peak_mag_db). No CFAR/close-in masking (unlike process_cpi, which
    overwrites the first CFAR_MASK_N bins - exactly the leakage we're trying to see here).
    Un-normalized (not peak-relative like the RD map convention) so magnitude is directly
    comparable across delay steps, not just within one frame."""
    chirp = if_signal[: ctx.N_chirp_samples]
    windowed = chirp * np.blackman(
        ctx.N_chirp_samples
    )  # matches process_cpi's range_window
    spectrum = np.fft.fft(windowed)
    mag_db = 20 * np.log10(np.abs(spectrum) + 1e-12)
    mag_db_pos = mag_db[ctx.pos]  # same positive-range mask as process_cpi
    peak_bin = int(np.argmax(mag_db_pos))
    return mag_db_pos, peak_bin, ctx.ranges_pos[peak_bin], mag_db_pos[peak_bin]


def run_sweep(radio, ctl, ctx, delays, n_captures):
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
            mag_db_pos, _, _, _ = range_profile_db(rx, ctx)
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


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def compare(rd_sw, rd_fb, ranges, velocities, save_plot):
    diff = rd_fb - rd_sw
    max_abs = np.max(np.abs(diff))
    rms = np.sqrt(np.mean(diff**2))

    sw_peak = np.unravel_index(np.argmax(rd_sw), rd_sw.shape)
    fb_peak = np.unravel_index(np.argmax(rd_fb), rd_fb.shape)
    peak_match = sw_peak == fb_peak

    print(f"\n--- Comparison ---")
    print(f"  Max |diff|:   {max_abs:.2f} dB")
    print(f"  RMS diff:     {rms:.2f} dB")
    print(f"  SW peak bin:  {sw_peak}")
    print(f"  Fab peak bin: {fb_peak}")
    print(f"  Peak match:   {'YES' if peak_match else 'NO'}")

    passed = max_abs < 6.0 and peak_match
    print(f"\n  {'PASS' if passed else 'FAIL'}")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not available, skipping plot)")
        return passed

    extent = [velocities[0], velocities[-1], ranges[0], ranges[-1]]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    for ax, data, title in [
        (axes[0], rd_sw, "Software dechirp"),
        (axes[1], rd_fb, "Fabric dechirp"),
    ]:
        ax.imshow(
            data.T,
            aspect="auto",
            origin="lower",
            extent=extent,
            vmin=-80,
            vmax=0,
            cmap="viridis",
        )
        ax.set_title(title)
        ax.set_xlabel("Velocity [m/s]")
        ax.set_ylabel("Range [m]")

    im = axes[2].imshow(
        diff.T, aspect="auto", origin="lower", extent=extent, cmap="RdBu_r"
    )
    axes[2].set_title(f"Diff (max {max_abs:.1f} dB)")
    axes[2].set_xlabel("Velocity [m/s]")
    axes[2].set_ylabel("Range [m]")
    fig.colorbar(im, ax=axes[2], label="dB")

    status = "PASS" if passed else "FAIL"
    fig.suptitle(f"I4 Dechirp Verification -- {status}")
    plt.tight_layout()

    if save_plot:
        plt.savefig(save_plot, dpi=150)
        print(f"  Plot saved: {save_plot}")
    plt.show()

    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="I4 fabric dechirp verification")
    ap.add_argument("--ip", default="192.168.5.10")
    ap.add_argument(
        "--delay",
        type=int,
        default=None,
        help="dechirp delay in samples (default: config FABRIC_DECHIRP_DELAY, "
        "or --sweep-start in sweep mode)",
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

    cfg = config.RadarConfig()
    ctx = dsp.build_cpi_context(a_config=cfg)
    ctl = FabricCtl(SshDevmem(args.ip))

    print("I4 Dechirp Verification")
    print(f"  IP:     {args.ip}")
    print(f"  FS:     {cfg.FS / 1e6:.1f} MHz")
    print(f"  BW:     {cfg.CHIRP_BW_HZ / 1e6:.1f} MHz")
    print(f"  T:      {cfg.CHIRP_DUR_S * 1e6:.0f} us")
    print(f"  Chirps: {cfg.CHIRP_REPS}")
    print(f"  Triangle: {cfg.TRIANGLE_EN}")

    print("\nChecking firmware...")
    ctl.check_magic()
    print("  MAGIC OK")

    print("\nConnecting SDR...")
    radio = AntSDR(a_radar_config=cfg)
    radio.set_loopback(args.loopback)

    try:
        radio.start()

        # Phase A
        print("\n--- Phase A: software dechirp ---")
        rd_sw, measured_delay, ranges, velocities, raw_sw = capture_sw(
            radio, cfg, ctx, args.captures
        )

        # Phase A's measured_delay is estimate_chirp_offset: a frame-sync offset
        # into the RX block, NOT a TX->RX dechirp latency, and routinely well
        # past the 128-sample delay line (it used to wrap silently, 349 -> 93).
        # So it is printed for reference only. Defaults: the measured loopback
        # constant for Phase B, and the first swept value for --sweep, whose
        # first set_calib_delay() overwrites it one iteration later anyway.
        if args.delay is not None:
            delay = args.delay
        elif args.sweep:
            delay = args.sweep_start
        else:
            delay = cfg.FABRIC_DECHIRP_DELAY
        print(
            f"\n  Dechirp delay for fabric: {delay} samples "
            f"(Phase A chirp offset was {measured_delay})"
        )

        fab_cfg = dataclasses.replace(
            cfg, FABRIC_DECHIRP_EN=True, FABRIC_DECHIRP_DELAY=delay
        )
        image = fabric_regs.register_image(fab_cfg)

        if args.sweep:
            # Fabric is enabled ONCE here, with `delay` as the starting value.
            # The sweep loop below only ever calls ctl.set_calib_delay() -
            # CTRL/nco_en/tx_src/sync_src are never touched again, unlike the
            # old per-value disable/reconfigure/enable cycle (suspected DAC
            # instability source, and the cause of the --delay 8 hang).
            print("\n--- Sweep: fabric dechirp delay ---")
            ctl.enable(image)
            _print_fabric_config(image, delay)
            run_sweep(
                radio,
                ctl,
                ctx,
                range(args.sweep_start, args.sweep_stop, args.sweep_step),
                args.captures,
            )
            print("\nRestoring passthrough...")
            ctl.disable()
            return

        # Phase B
        print("\n--- Phase B: fabric dechirp ---")
        ctl.enable(image)
        _print_fabric_config(image, delay)
        rd_fb, _, _, raw_fb = capture_fabric(radio, cfg, ctx, args.captures)

        # Restore
        print("\nRestoring passthrough...")
        ctl.disable()

        # Save raw data
        if args.save:
            np.savez(
                "dechirp_verify.npz",
                rd_sw=rd_sw,
                rd_fb=rd_fb,
                ranges=ranges,
                velocities=velocities,
                delay=delay,
                raw_sw_0=raw_sw[0],
                raw_fb_0=raw_fb[0],
            )
            print("  Saved: dechirp_verify.npz")

        # Compare
        passed = compare(
            rd_sw, rd_fb, ranges, velocities, args.plot if args.plot else None
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
