#!/usr/bin/env python3
"""
I5 steps 7-8: ramp through the fabric decimator, bit-exact vs decimate_golden.

Usage (venv active):
  cd src/python
  python ../../scripts/bringup/decim_ramp_check.py          # step 7, sync_src=0
  python ../../scripts/bringup/decim_ramp_check.py --sync   # step 8, prints FABRIC_FRAME_TRIM
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src", "python"))
sys.path.insert(
    0,
    os.path.join(
        HERE,
        "..",
        "..",
        "firmware",
        "plutosdr-fw",
        "hdl",
        "projects",
        "e200",
        "scripts",
    ),
)

from common import config, fabric_regs as fr
from online.fabric_ctl import SshDevmem, FabricCtl
from online.sdr import AntSDR
import decimate_reference as dref


def golden_period(a_n: int) -> np.ndarray:
    """One steady-state leg of decimated ramp, starting at the period-tagged output."""
    x = np.tile(np.arange(a_n, dtype=np.int64), 4)
    y, tags = dref.decimate_golden(x, dref.load_taps(), list(range(0, 4 * a_n, a_n)))
    g = y[tags[-1] :].astype(np.int16)
    assert len(g) == a_n // 8, f"golden leg {len(g)} != {a_n // 8}"
    return g


def match_phase(a_y: np.ndarray, a_g: np.ndarray) -> tuple[int, int]:
    """Best k with y[i] == g[(i + k) % P]; returns (k, mismatch count)."""
    p = len(a_g)
    idx = np.arange(len(a_y))
    best = (0, len(a_y))
    for k in range(p):
        bad = int(np.count_nonzero(a_y != a_g[(idx + k) % p]))
        if bad < best[1]:
            best = (k, bad)
            if bad == 0:
                break
    return best


def main():
    ap = argparse.ArgumentParser(description="I5 decimator ramp check")
    ap.add_argument("--ip", default="192.168.5.10")
    ap.add_argument("--sync", action="store_true", help="sync_src=1 (step 8)")
    ap.add_argument("--blocks", type=int, default=3)
    args = ap.parse_args()

    cfg = config.RadarConfig(
        FABRIC_DECHIRP_EN=True, FABRIC_DECIM_EN=True, TRIANGLE_EN=False
    )
    n = cfg.SWEEP_LEN
    g = golden_period(n)
    print(f"SWEEP_LEN={n}, N_IF={cfg.N_IF}, golden leg {len(g)} samples")

    transport = SshDevmem(args.ip)
    ctl = FabricCtl(transport)
    ctl.check_magic()
    print("MAGIC OK")

    img = fr.register_image(cfg)
    ctrl_run = fr.CTRL_RAMP_EN | fr.CTRL_NCO_EN | (fr.CTRL_SYNC_SRC if args.sync else 0)
    seq = [
        (fr.REG_FTW_START, img[fr.REG_FTW_START]),
        (fr.REG_FTW_SLOPE, img[fr.REG_FTW_SLOPE]),
        (fr.REG_SWEEP_LEN, img[fr.REG_SWEEP_LEN]),
        (fr.REG_CTRL, 0),
        (fr.REG_COMMIT, 1),
        (fr.REG_CTRL, ctrl_run),
        (fr.REG_DECIM_SEL, fr.DECIM_SEL_DECIMATE),
    ]

    radio = AntSDR(a_radar_config=cfg)
    radio.set_loopback(True)
    ok = True
    try:
        radio.start()
        transport.write_seq(seq)
        print(f"CTRL=0x{ctrl_run:02X}, DECIM_SEL=1, IF_SEL=0")
        for _ in range(6):  # kernel buffers filled before the switch
            radio._read_raw()

        for b in range(args.blocks):
            raw = np.frombuffer(radio._read_raw(), dtype=np.int16)
            i_ch, q_ch = raw[0::2], raw[1::2]
            if not np.array_equal(i_ch, q_ch):
                print(f"block {b}: I != Q")
                ok = False
            k, bad = match_phase(i_ch, g)
            trim = (-k) % len(g)
            steps, counts = np.unique(
                np.diff(i_ch.astype(np.int32)), return_counts=True
            )
            common = dict(
                zip(steps[np.argsort(-counts)][:3], np.sort(counts)[::-1][:3])
            )
            print(
                f"block {b}: {len(i_ch)} samples, mismatches {bad}, "
                f"first tagged sample at index {trim}, top steps {common}"
            )
            ok &= bad == 0

        print(f"\n{'PASS' if ok else 'FAIL'}: bit-exact vs decimate_golden")
        if args.sync:
            print(f"FABRIC_FRAME_TRIM = {trim} (expect 0, 1 is legal)")
    finally:
        try:
            ctl.disable()
        except Exception as e:
            print(f"WARNING: fabric cleanup failed: {e}")
        radio.set_loopback(False)
        radio.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
