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
    T_rep = a_cfg.LEGS_PER_PERIOD * a_cfg.T_EFF
    vel_res = (config.c / a_cfg.CHIRP_FC_HZ) / (2 * T_rep * a_cfg.CHIRP_REPS)
    for d in a_detections:
        if a_kind is not None and d["kind"] != a_kind:
            continue
        if abs(d["r"] - a_r) <= 2 * range_res and abs(d["v"] - a_v) <= 2 * vel_res:
            return d
    return None


def noise_floor_block(a_cfg, a_ctx):
    # One CPI of IF rows, as capture_rx_data slices it (fabric /8 included)
    n = a_ctx.N_if_samples * a_cfg.LEGS_PER_PERIOD * a_cfg.CHIRP_REPS
    rng = np.random.default_rng(SEED)
    return (1e-3 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(
        np.complex64
    )


def synth(a_cfg, a_targets):
    ctx = dsp.build_cpi_context(a_cfg)
    if_raw = noise_floor_block(a_cfg, ctx)
    # Seed the loopback noise too, or this test is flaky (occasional spurious
    # CFAR false alarms from run to run).
    ts = TargetSim(a_seed=SEED)
    ts.set_targets(a_targets)
    return ts.apply_if(a_if_raw=if_raw, a_config=a_cfg, a_ctx=ctx), ctx


def detect(a_cfg, a_targets):
    if_out, ctx = synth(a_cfg, a_targets)
    _, _, detections, _, _ = dsp.process_cpi(if_out, a_cfg, ctx)
    return detections


def detect_down_only(a_cfg, a_targets):
    """detect() with the echo's up legs blanked: the target is in the down maps only.
    Needs SDR_LOOPBACK_EN off, so apply_if adds the echo and nothing else."""
    ctx = dsp.build_cpi_context(a_cfg)
    noise = noise_floor_block(a_cfg, ctx)
    ts = TargetSim()
    ts.set_targets(a_targets)
    echo = ts.apply_if(a_if_raw=noise, a_config=a_cfg, a_ctx=ctx) - noise
    # Triangle rows alternate up, down
    echo.reshape(-1, ctx.N_if_samples)[0::2] = 0
    _, _, detections, _, _ = dsp.process_cpi(noise + echo, a_cfg, ctx)
    return detections


def tdm_config(a_triangle=False):
    return dataclasses.replace(
        config.RadarConfig(),
        FABRIC_DECHIRP_EN=True,
        TDM_EN=True,
        TRIANGLE_EN=a_triangle,
    )


def theta_without_doppler_term(a_if, a_cfg, a_ctx, a_det):
    """Angle at a_det's cell from the raw A-B phase, i.e. with the Doppler gap left in."""
    N = a_ctx.N_if_samples
    rows = a_if[: a_cfg.LEGS_PER_PERIOD * a_cfg.CHIRP_REPS * N].reshape(-1, N)
    XA = np.fft.fftshift(np.fft.fft2(rows[0::2] * a_ctx.window_2d), axes=0)
    XB = np.fft.fftshift(np.fft.fft2(rows[1::2] * a_ctx.window_2d), axes=0)
    row = np.argmin(np.abs(a_ctx.velocities - a_det["v"]))
    col = np.argmin(np.abs(a_ctx.ranges_pos - a_det["r"]))
    psi = np.angle(XB[row, col] * np.conj(XA[row, col]))
    lam = config.c / a_cfg.CHIRP_FC_HZ
    s = -psi * lam / (2 * np.pi * a_cfg.TDM_TX_ANT_SEP_M)
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


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


def check_tdm_angle(a_cfg, a_kind, a_detect=detect):
    """theta0 in {-30, 0, +30} x v0 in {-20, 0, +20} comes back within 1 deg."""
    errors = []
    for theta0 in (-30.0, 0.0, 30.0):
        for v0 in (-20.0, 0.0, 20.0):
            target = {
                "r0": 300.0,
                "v0": v0,
                "a0": 0.0,
                "theta0": theta0,
                "amp": 5.0,
                "duration": 100.0,
            }
            d = find_match(a_detect(a_cfg, [target]), 300.0, v0, a_cfg, a_kind)
            assert d is not None, (theta0, v0)
            if abs(d["theta"] - theta0) >= 1.0:
                errors.append((theta0, v0, d["theta"]))
    assert not errors, errors


def test_tdm_angle():
    # K10 step 4
    check_tdm_angle(tdm_config(), "up")


def test_tdm_triangle_angle():
    # K10 step 5: a clean target pairs as "both", angle from up + down maps.
    # MAX_VELOCITY is 32 m/s here, so +-20 stays inside it.
    check_tdm_angle(tdm_config(a_triangle=True), "both")


def test_tdm_triangle_down_only_angle():
    # Down-only detections read the conj-built down maps on their own rows
    cfg = dataclasses.replace(tdm_config(a_triangle=True), SDR_LOOPBACK_EN=False)
    check_tdm_angle(cfg, "down", detect_down_only)


def test_tdm_doppler_term_is_live():
    # Without the Doppler term a target at 20 m/s reads ~9 deg off (K10 exercise),
    # so the 1 deg bar above is only met with the term at work
    cfg = tdm_config()
    if_out, ctx = synth(
        cfg,
        [
            {
                "r0": 300.0,
                "v0": 20.0,
                "a0": 0.0,
                "theta0": 0.0,
                "amp": 5.0,
                "duration": 100.0,
            }
        ],
    )
    _, _, detections, _, _ = dsp.process_cpi(if_out, cfg, ctx)
    d = find_match(detections, 300.0, 20.0, cfg)
    assert d is not None, detections
    raw = theta_without_doppler_term(if_out, cfg, ctx, d)
    assert 8.0 < abs(raw) < 10.0, raw


def test_triangle_pairing():
    # The target must appear in BOTH maps and pair as kind="both",
    # not up-only or down-only.
    cfg = dataclasses.replace(config.RadarConfig(), TRIANGLE_EN=True)
    detections = detect(
        cfg,
        [{"r0": 400.0, "v0": 10.0, "a0": 0.0, "amp": 5.0, "duration": 100.0}],
    )
    assert (
        find_match(detections, 400.0, 10.0, cfg, a_kind="both") is not None
    ), detections


if __name__ == "__main__":
    tests = [
        test_stationary_target,
        test_moving_target_positive_velocity,
        test_moving_target_negative_velocity,
        test_tdm_angle,
        test_tdm_triangle_angle,
        test_tdm_triangle_down_only_angle,
        test_tdm_doppler_term_is_live,
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
