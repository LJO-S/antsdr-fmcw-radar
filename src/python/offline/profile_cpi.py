"""
Profiling for the CPI (Part K1, Phase 6 guide Section 2).
Noted that FPS drops when target detections increases, so we need to profile the CPI to find the bottleneck.

Hypothesis: Both FFT and CFAR run on fixed-size arrays, so probably the subbin-refinement.

Synthesizes a fabric /8 IF block with K fake targets (TargetSim.apply_if) and times
each stage of process_cpi, median over --runs. The stages come from a mirror of
process_cpi that is checked against the real one before timing, so a stale mirror
fails loudly instead of timing old code.

    python -m offline.profile_cpi [--k 0 5 20 50] [--runs 50] [--triangle] [--mti]
"""

import argparse
import time

import numpy as np
from scipy.ndimage import binary_dilation

import common.config as config
import common.dsp as dsp
from online.target_sim import TargetSim

SEED = 0
AMP = 5.0  # relative to the block RMS, as in test_target_sim


# ===================================================================================
class Laps:
    """Accumulates ms per stage name; each call closes the stage since the last call."""

    def __init__(self):
        self.ms = {}
        self.t = time.perf_counter()

    def __call__(self, a_name):
        now = time.perf_counter()
        self.ms[a_name] = self.ms.get(a_name, 0.0) + 1e3 * (now - self.t)
        self.t = now


# ===================================================================================
def fake_targets(a_k, a_cfg):
    """K targets on a 10 x 5 (range, velocity) lattice, spaced wider than the CFAR window."""
    targets = []
    for i in range(a_k):
        r = 40 + (i % 10) * (0.9 * a_cfg.MAX_RANGE - 40) / 9
        v = (-0.55 + 0.3 * (i // 10 % 5)) * a_cfg.MAX_VELOCITY
        targets.append({"r0": r, "v0": v, "a0": 0.0, "amp": AMP, "duration": 1e6})
    return targets


def if_block(a_cfg, a_ctx, a_sim):
    """Noise floor + fake targets, sized like capture_rx_data's fabric slice."""
    rows = 2 * a_cfg.CHIRP_REPS if a_cfg.TRIANGLE_EN else a_cfg.CHIRP_REPS
    n = rows * a_cfg.N_IF
    rng = np.random.default_rng(SEED)
    noise = 1e-3 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return a_sim.apply_if(noise.astype(np.complex64), a_cfg, a_ctx)


# ===================================================================================
def staged_process_cpi(a_if_signal, a_config, a_ctx, a_lap):
    """dsp.process_cpi split into timed stages. fft2 is split into its two passes."""
    N = a_ctx.N_if_samples
    pos = a_ctx.pos
    rng = a_ctx.ranges_pos
    vel = a_ctx.velocities
    tri = a_config.TRIANGLE_EN

    # Reshape, fast-time DC, MTI, window
    if tri:
        full = a_if_signal[: 2 * a_config.CHIRP_REPS * N].reshape(-1, N)
        mats = [full[0::2, :].copy(), full[1::2, :].copy()]
    else:
        mats = [a_if_signal[: a_config.CHIRP_REPS * N].reshape(-1, N).copy()]
    for m in mats:
        m -= m.mean(axis=1, keepdims=True)
        if a_config.MTI_EN:
            m -= m.mean(axis=0, keepdims=True)
        m *= a_ctx.window_2d
    if tri:
        mats[1] = np.conj(mats[1])
    a_lap("prep (DC/MTI/window)")

    mats = [np.fft.fft(m, axis=1) for m in mats]
    a_lap("range FFT")
    rds = [np.fft.fftshift(np.fft.fft(m, axis=0), axes=0) for m in mats]
    a_lap("Doppler FFT")

    for rd in rds:
        rd[:, : a_config.CFAR_MASK_N] = np.median(np.abs(rd))
    a_lap("close-in mask")

    dbs = [20 * np.log10(np.abs(rd[:, pos]) + 1e-12) for rd in rds]
    max_val = max(np.max(db) for db in dbs)
    for db in dbs:
        db -= max_val
    a_lap("dB map")

    cfars = [dsp.cfar_ca_2d(rd, a_config, a_apply_nms=False) for rd in rds]
    a_lap("CFAR")
    # nsize=3 matches the hardcoded value inside cfar_ca_2d
    dets = [dsp.nms(det, pwr, a_nsize=3) for det, pwr in cfars]
    pwrs = [pwr for _, pwr in cfars]
    a_lap("NMS")

    rng_bw = rng[1] - rng[0]
    vel_bw = vel[1] - vel[0]

    if tri:
        up_mask_full = dets[0][:, pos]
        up_pwr_pos = pwrs[0][:, pos]
        down_mask_full = dsp.align_down_doppler(dets[1])[:, pos]
        down_pwr_pos = dsp.align_down_doppler(pwrs[1])[:, pos]
        avg_pwr = (up_pwr_pos + down_pwr_pos) / 2
        PAIR_TOL = 2
        both_mask = up_mask_full & binary_dilation(down_mask_full, iterations=PAIR_TOL)
        both_dil = binary_dilation(both_mask, iterations=PAIR_TOL)
        up_mask = up_mask_full & ~both_dil
        down_mask = down_mask_full & ~both_dil
        a_lap("pairing")
        groups = [
            ("both", avg_pwr, both_mask),
            ("up", up_pwr_pos, up_mask),
            ("down", down_pwr_pos, down_mask),
        ]
    else:
        groups = [("up", pwrs[0][:, pos], dets[0][:, pos])]

    refined = []
    for kind, pwr, mask in groups:
        dop, rbin = np.where(mask)
        row_off, col_off = dsp.subbin_refine(pwr, dop, rbin)
        refined.append((kind, rng[rbin] + col_off * rng_bw, vel[dop] + row_off * vel_bw))
    a_lap("sub-bin refine")

    targets = [
        {"r": r, "v": v, "kind": kind}
        for kind, rs, vs in refined
        for r, v in zip(rs, vs)
    ]
    a_lap("target dicts")

    return dbs[0], (dbs[1] if tri else None), targets


def check_mirror(a_if, a_cfg, a_ctx):
    db_up, db_down, targets, _, _ = dsp.process_cpi(a_if, a_cfg, a_ctx)
    m_up, m_down, m_targets = staged_process_cpi(a_if, a_cfg, a_ctx, Laps())
    msg = "staged_process_cpi no longer matches dsp.process_cpi - update the mirror"
    assert np.allclose(db_up, m_up, atol=1e-3), msg
    if db_down is not None:
        assert np.allclose(db_down, m_down, atol=1e-3), msg
    assert len(targets) == len(m_targets), msg
    for a, b in zip(targets, m_targets):
        assert a["kind"] == b["kind"], msg
        assert np.isclose(a["r"], b["r"]) and np.isclose(a["v"], b["v"]), msg


# ===================================================================================
def profile(a_cfg, a_k, a_runs, a_warmup=3):
    """Median ms per stage for K targets, plus apply_if and the real process_cpi."""
    ctx = dsp.build_cpi_context(a_cfg)
    sim = TargetSim()
    sim.set_targets(fake_targets(a_k, a_cfg))
    # apply_noise uses the legacy global RNG
    np.random.seed(SEED)
    if_sig = if_block(a_cfg, ctx, sim)
    check_mirror(if_sig, a_cfg, ctx)

    samples = {}
    for i in range(a_warmup + a_runs):
        lap = Laps()
        staged_process_cpi(if_sig, a_cfg, ctx, lap)
        t0 = time.perf_counter()
        _, _, targets, _, _ = dsp.process_cpi(if_sig, a_cfg, ctx)
        t1 = time.perf_counter()
        # Timed on its own block copy: targets move with wall time, the IF above does not
        if_block(a_cfg, ctx, sim)
        t2 = time.perf_counter()
        if i < a_warmup:
            continue
        lap.ms["process_cpi (real)"] = 1e3 * (t1 - t0)
        lap.ms["apply_if (capture)"] = 1e3 * (t2 - t1)
        for name, ms in lap.ms.items():
            samples.setdefault(name, []).append(ms)

    med = {name: float(np.median(v)) for name, v in samples.items()}
    return med, len(targets)


def print_table(a_ks, a_results):
    stages = list(a_results[0][0].keys())
    real = ["process_cpi (real)", "apply_if (capture)"]
    mirror = [s for s in stages if s not in real]
    w = max(len(s) for s in stages + ["sum of stages"]) + 2

    def row(a_name, a_vals, a_fmt="{:9.2f}"):
        print(f"{a_name:<{w}}" + "".join(a_fmt.format(v) for v in a_vals))

    row("K fake targets", a_ks, "{:9d}")
    row("detections", [n for _, n in a_results], "{:9d}")
    print("-" * (w + 9 * len(a_ks)) + "  (median ms)")
    for s in mirror:
        row(s, [m.get(s, 0.0) for m, _ in a_results])
    print("-" * (w + 9 * len(a_ks)))
    row("sum of stages", [sum(m[s] for s in mirror) for m, _ in a_results])
    for s in real:
        row(s, [m[s] for m, _ in a_results])
    cpi_s = [1e3 / (m[real[0]] + m[real[1]]) for m, _ in a_results]
    row("CPI/s bound (both)", cpi_s, "{:9.1f}")


# ===================================================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="process_cpi stage timing vs target count")
    ap.add_argument("--k", type=int, nargs="+", default=[0, 5, 20, 50])
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--triangle", action="store_true")
    ap.add_argument("--mti", action="store_true")
    args = ap.parse_args()

    cfg = config.RadarConfig(
        FABRIC_DECHIRP_EN=True, TRIANGLE_EN=args.triangle, MTI_EN=args.mti
    )
    print(
        f"Fabric /{config.FABRIC_DECIMATE_RATIO}: N_IF={cfg.N_IF}, "
        f"CHIRP_REPS={cfg.CHIRP_REPS}, triangle={cfg.TRIANGLE_EN}, MTI={cfg.MTI_EN}, "
        f"runs={args.runs}\n"
    )
    results = [profile(cfg, k, args.runs) for k in args.k]
    print_table(args.k, results)
