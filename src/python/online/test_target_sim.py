"""
online/test_target_sim.py

No-radio tests for TargetSim.apply_if() - synthesizing fake targets directly
onto an already-dechirped IF stream. Each test builds a
low-level noise-floor IF block, injects one target via apply_if(), and checks
dsp.process_cpi() recovers it within tolerance.

Deliberately NOT offline/test_soft_model.py's match_detections(): that helper
fails on any unmatched leftover detection, which is the right bar for its
realistic multi-target physical simulation but not for a bare i.i.d. Gaussian
noise floor here - CA-CFAR at pfa=1e-6 over ~N_chirp_samples*CHIRP_REPS cells
has a nonzero expected false-alarm count on pure noise by construction, unrelated
to whether apply_if() is correct. These tests only assert the injected target
is found within tolerance among the detections, extras or not.
"""

import dataclasses

import numpy as np

import common.config as config
import common.dsp as dsp
from online.target_sim import TargetSim

SEED = 0


def find_match(a_detections, a_r, a_v, a_cfg, a_kind=None):
    range_res = config.c / (2 * a_cfg.CHIRP_BW_HZ)
    T_rep = 2 * a_cfg.CHIRP_DUR_S if a_cfg.TRIANGLE_EN else a_cfg.CHIRP_DUR_S
    vel_res = (config.c / a_cfg.CHIRP_FC_HZ) / (2 * T_rep * a_cfg.CHIRP_REPS)
    for d in a_detections:
        if a_kind is not None and d["kind"] != a_kind:
            continue
        if abs(d["r"] - a_r) <= 2 * range_res and abs(d["v"] - a_v) <= 2 * vel_res:
            return d
    return None


def noise_floor_block(a_cfg, a_ctx, a_chirp_mult=1):
    n = a_ctx.N_chirp_samples * a_cfg.CHIRP_REPS * a_chirp_mult
    rng = np.random.default_rng(SEED)
    return (1e-3 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(
        np.complex64
    )


def detect(a_cfg, a_targets, a_chirp_mult=1):
    ctx = dsp.build_cpi_context(a_cfg)
    if_raw = noise_floor_block(a_cfg, ctx, a_chirp_mult)
    # dsp.apply_noise (called inside apply_if when SDR_LOOPBACK_EN) draws from
    # the legacy global np.random, not a seeded Generator - seed it here too,
    # same pattern offline/test_soft_model.py uses, or this test is flaky
    # (occasional spurious CFAR false alarms from run to run).
    np.random.seed(SEED)
    ts = TargetSim()
    ts.set_targets(a_targets)
    if_out = ts.apply_if(a_if_raw=if_raw, a_config=a_cfg, a_ctx=ctx)
    _, _, detections, _, _ = dsp.process_cpi(if_out, a_cfg, ctx)
    return detections


def test_stationary_target():
    cfg = config.RadarConfig()
    detections = detect(
        cfg, [{"r0": 500.0, "v0": 0.0, "a0": 0.0, "amp": 5.0, "duration": 100.0}]
    )
    assert find_match(detections, 500.0, 0.0, cfg) is not None, detections


def test_moving_target_positive_velocity():
    # TODO I4c-1 step 6(2): v0=+20 must land in the correct velocity bin with
    # the correct sign. Regression test for the Doppler-sign bug found
    # 2026-09-13: dsp.apply_doppler_shift needs a NEGATED velocity when used
    # in the IF domain (see target_sim.py, apply_if) because mix_signal's
    # conj(RX) - which flips the sign back in the baseband path - never runs
    # here.
    cfg = config.RadarConfig()
    detections = detect(
        cfg, [{"r0": 300.0, "v0": 20.0, "a0": 0.0, "amp": 5.0, "duration": 100.0}]
    )
    assert find_match(detections, 300.0, 20.0, cfg) is not None, detections


def test_moving_target_negative_velocity():
    # Sign-check the other direction - a plain missing negation, or a
    # negation on the wrong operand, passes one direction and fails the
    # other.
    cfg = config.RadarConfig()
    detections = detect(
        cfg, [{"r0": 300.0, "v0": -20.0, "a0": 0.0, "amp": 5.0, "duration": 100.0}]
    )
    assert find_match(detections, 300.0, -20.0, cfg) is not None, detections


def test_triangle_pairing():
    # The target must appear in BOTH maps and pair as kind="both",
    # not up-only or down-only.
    cfg = dataclasses.replace(config.RadarConfig(), TRIANGLE_EN=True)
    detections = detect(
        cfg,
        [{"r0": 400.0, "v0": 10.0, "a0": 0.0, "amp": 5.0, "duration": 100.0}],
        a_chirp_mult=2,
    )
    assert (
        find_match(detections, 400.0, 10.0, cfg, a_kind="both") is not None
    ), detections


if __name__ == "__main__":
    tests = [
        test_stationary_target,
        test_moving_target_positive_velocity,
        test_moving_target_negative_velocity,
        test_triangle_pairing,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")

    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
