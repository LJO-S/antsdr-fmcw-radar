#!/usr/bin/env python3
"""
I4 fabric dechirp verification: capture-and-compare.

Two-phase test on digital loopback (or cable):
  Phase A  raw RX -> software frame_sync + mix_signal + process_cpi
  Phase B  fabric NCO TX + dechirp -> process_cpi (no mix_signal)

Compares the two RD maps.  Writes fmcw_core AXI-Lite registers via
SSH + devmem; requires sshpass and root@<ip> SSH access (password
"analog", standard on AntSDR).

Prerequisites:
  apt install sshpass   # on the host

Usage (from repo root, venv active):
  cd src/python
  python ../../scripts/bringup/dechirp_verify.py [--delay D] [--ip IP]

  --delay D   fabric dechirp delay in samples (default: auto-measured
              from Phase A chirp offset)
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
import shutil
import subprocess

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "python"))

from common import config, dsp
from common.fabric_regs import (
    REG_MAGIC,
    REG_CTRL,
    REG_COMMIT,
    REG_FTW_START,
    REG_FTW_SLOPE,
    REG_SWEEP_LEN,
    REG_DECHIRP_DELAY,
    REG_IF_SEL,
    REG_STATUS,
    MAGIC,
    CTRL_NCO_EN,
    CTRL_TX_SRC,
    CTRL_SYNC_SRC,
    CTRL_TRIANGLE_EN,
    IF_SEL_IF_OUT,
    STATUS_DAC_EN0,
    chirp_ftw,
)
from online.sdr import AntSDR

FMCW_CORE_BASE = 0x43C10000
SDR_PASSWORD = "analog"


# ---------------------------------------------------------------------------
# Register access via SSH + devmem
# ---------------------------------------------------------------------------
def _check_sshpass():
    if shutil.which("sshpass") is None:
        print("ERROR: sshpass not found. Install it:  apt install sshpass")
        sys.exit(1)


def _ssh(ip, cmd):
    r = subprocess.run(
        [
            "sshpass",
            "-p",
            SDR_PASSWORD,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=5",
            f"root@{ip}",
            cmd,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ssh cmd failed: {cmd}\nstderr: {r.stderr.strip()}")
    return r.stdout.strip()


def read_reg(ip, offset):
    addr = FMCW_CORE_BASE + offset
    out = _ssh(ip, f"devmem 0x{addr:08X} 32")
    return int(out, 0)


def write_reg(ip, offset, value):
    addr = FMCW_CORE_BASE + offset
    _ssh(ip, f"devmem 0x{addr:08X} 32 0x{value & 0xFFFFFFFF:08X}")


# ---------------------------------------------------------------------------
# Fabric register helpers
# ---------------------------------------------------------------------------
def check_magic(ip):
    m = read_reg(ip, REG_MAGIC)
    if m != MAGIC:
        raise RuntimeError(
            f"MAGIC mismatch: read 0x{m:08X}, expected 0x{MAGIC:08X}. "
            "Wrong firmware image?"
        )
    print(f"  MAGIC OK (0x{m:08X})")


def configure_fabric_dechirp(ip, cfg, delay):
    """Write fmcw_core registers for fabric dechirp mode."""
    ftw_start, ftw_slope, sweep_len = chirp_ftw(
        cfg.CHIRP_BW_HZ, cfg.CHIRP_DUR_S, cfg.FS
    )

    # TRIANGLE_EN must be written before COMMIT to latch with this shadow set
    # (hardware-observed exception, I2 bring-up 2026-08-30). TX_SRC/SYNC_SRC
    # are quasi-static (their own synchronizers, no COMMIT needed) - do NOT
    # write them here: setting SYNC_SRC=1 before NCO_EN=1 is the documented
    # "RX capture hangs forever" trap (chirp_start pulses don't exist yet, so
    # a DMA sync wait would never complete).
    ctrl_pre = CTRL_TRIANGLE_EN if cfg.TRIANGLE_EN else 0

    # REG_CHIRP_COUNT is read-only in RTL (write case has no x"1C" arm) - don't
    # write it, it's a silent no-op.
    write_reg(ip, REG_FTW_START, ftw_start)
    write_reg(ip, REG_FTW_SLOPE, ftw_slope)
    write_reg(ip, REG_SWEEP_LEN, sweep_len)
    write_reg(ip, REG_DECHIRP_DELAY, delay)
    write_reg(ip, REG_IF_SEL, IF_SEL_IF_OUT)
    write_reg(ip, REG_CTRL, ctrl_pre)
    write_reg(ip, REG_COMMIT, 1)

    # TX_SRC, SYNC_SRC and NCO_EN land in one atomic write, after COMMIT
    # (nco_en with an uncommitted SWEEP_LEN wedges the core) - so the fabric
    # never sits in the sync_src=1/nco_en=0 hang state. CTRL_RAMP_EN must NOT
    # be set here - it outranks IF_SEL in the ADC mux and would silently
    # replace the IF stream with the debug counter.
    ctrl = ctrl_pre | CTRL_TX_SRC | CTRL_SYNC_SRC | CTRL_NCO_EN
    write_reg(ip, REG_CTRL, ctrl)

    print(
        f"  Fabric configured: delay={delay}, ctrl=0x{ctrl:02X}, sweep_len={sweep_len}"
    )

    # Settle + sanity check: reading STATUS costs one more SSH round trip
    # (~100-300 ms), which happens to be exactly the margin that was missing
    # before the very first RX buffer refill() after a cold enable - without
    # it, capture_fabric's flush loop could catch the DMA sync chain still
    # spinning up and hang on refill() (ETIMEDOUT). dac_enable_i0=0 here also
    # means the NCO output is being discarded at the DAC mux (guide S1) - a
    # clear diagnostic instead of a silent downstream hang.
    status = read_reg(ip, REG_STATUS)
    if not (status & STATUS_DAC_EN0):
        print(
            f"  WARNING: STATUS=0x{status:08X} - dac_enable_i0 not set, "
            "NCO output may be discarded at the DAC mux"
        )


def disable_fabric(ip):
    """Restore passthrough (NCO off, IF_SEL off)."""
    write_reg(ip, REG_CTRL, 0)
    write_reg(ip, REG_IF_SEL, 0)
    write_reg(ip, REG_COMMIT, 1)
    print("  Fabric restored to passthrough")


def set_dechirp_delay(ip, delay):
    """Reload DECHIRP_DELAY only. Fabric must already be enabled (configure_fabric_dechirp
    already ran) - this never touches CTRL, so nco_en/tx_src/sync_src are left running.
    DECHIRP_DELAY latches on the same commit pulse as FTW_START/SLOPE/SWEEP_LEN, but that
    pulse is independent of CTRL, so re-committing while the NCO is live just reloads the
    mixer's delay line at the next chirp boundary (~100 us later, well under one SSH
    round trip) - see fmcw_core.vhd's r_cfg_valid wiring."""
    write_reg(ip, REG_DECHIRP_DELAY, delay)
    write_reg(ip, REG_COMMIT, 1)


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


def run_sweep(radio, ip, ctx, delays, n_captures):
    """Sweep DECHIRP_DELAY over `delays` with the fabric enabled exactly once (by the
    caller, before this runs). Prints one row per delay - no CTRL writes happen in here.
    """
    print(f"\n{'delay':>6} {'peak_bin':>9} {'range_m':>9} {'peak_dB':>9}")
    rows = []
    for delay in delays:
        set_dechirp_delay(ip, delay)
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
        help="dechirp delay in samples (default: auto from Phase A)",
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

    _check_sshpass()

    cfg = config.RadarConfig()
    ctx = dsp.build_cpi_context(a_config=cfg)

    print("I4 Dechirp Verification")
    print(f"  IP:     {args.ip}")
    print(f"  FS:     {cfg.FS / 1e6:.1f} MHz")
    print(f"  BW:     {cfg.CHIRP_BW_HZ / 1e6:.1f} MHz")
    print(f"  T:      {cfg.CHIRP_DUR_S * 1e6:.0f} us")
    print(f"  Chirps: {cfg.CHIRP_REPS}")
    print(f"  Triangle: {cfg.TRIANGLE_EN}")

    print("\nChecking firmware...")
    check_magic(args.ip)

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

        # measured_delay comes from the DMA-TX path (Phase A); the fabric NCO
        # injects post-interpolator, so its TX->RX latency differs and this
        # auto value is only a starting guess - expect to need --delay swept
        # by hand (see run_plan.txt).
        delay = args.delay if args.delay is not None else measured_delay
        print(f"\n  Dechirp delay for fabric: {delay} samples")

        if args.sweep:
            # Fabric is enabled ONCE here, with `delay` as the starting value.
            # The sweep loop below only ever calls set_dechirp_delay() -
            # CTRL/nco_en/tx_src/sync_src are never touched again, unlike the
            # old per-value disable/reconfigure/enable cycle (suspected DAC
            # instability source, and the cause of the --delay 8 hang).
            print("\n--- Sweep: fabric dechirp delay ---")
            configure_fabric_dechirp(args.ip, cfg, delay)
            run_sweep(
                radio,
                args.ip,
                ctx,
                range(args.sweep_start, args.sweep_stop, args.sweep_step),
                args.captures,
            )
            print("\nRestoring passthrough...")
            disable_fabric(args.ip)
            return

        # Phase B
        print("\n--- Phase B: fabric dechirp ---")
        configure_fabric_dechirp(args.ip, cfg, delay)
        rd_fb, _, _, raw_fb = capture_fabric(radio, cfg, ctx, args.captures)

        # Restore
        print("\nRestoring passthrough...")
        disable_fabric(args.ip)

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
            disable_fabric(args.ip)
        except Exception as e:
            print(f"  WARNING: fabric cleanup failed: {e}")
        radio.set_loopback(False)
        radio.close()


if __name__ == "__main__":
    main()
