"""
common/test_dsp.py

Board-free tests for the circular Doppler axis in dsp.py: box_sum, nms,
subbin_refine, wrap_velocity, dilate_doppler, and process_cpi on targets at the
edge of the velocity span (where one peak straddles rows REPS-1 and 0).
"""

import dataclasses

import numpy as np
from scipy.ndimage import binary_dilation, maximum_filter

import common.config as config
import common.dsp as dsp
from online.target_sim import TargetSim

REPS = 128


def peak_map(
    a_row_frac, a_col, a_shape=(REPS, 200), a_row0=0, a_sigma=1.0, a_floor=1e-6
):
    """Gaussian peak at (a_row0 + a_row_frac, a_col) on a floor, rows circular."""
    rows = np.arange(a_shape[0])[:, None]
    cols = np.arange(a_shape[1])[None, :]
    dr = (rows - (a_row0 + a_row_frac) + a_shape[0] / 2) % a_shape[0] - a_shape[0] / 2
    return a_floor + np.exp(-(dr**2 + (cols - a_col) ** 2) / (2 * a_sigma**2))


def test_box_sum_matches_brute_force():
    rng = np.random.default_rng(0)
    x = rng.random((REPS, 60)).astype(np.float32)
    for size in (9, 19):
        h = size // 2
        pad = np.pad(x.astype(np.float64), ((h, h), (0, 0)), mode="wrap")
        pad = np.pad(pad, ((0, 0), (h, h)), mode="edge")
        ref = np.zeros(x.shape)
        for i in range(size):
            for j in range(size):
                ref += pad[i : i + x.shape[0], j : j + x.shape[1]]
        got = dsp.box_sum(x, size)
        assert got.dtype == np.float64
        # Same terms, different summation order: float64 rounding only
        assert np.allclose(got, ref, rtol=1e-12, atol=0), np.abs(got - ref).max()


def test_nms_straddling_peak_is_one_detection():
    pwr = peak_map(-0.5, 50, a_row0=0)  # equal power on rows REPS-1 and 0
    pwr[REPS - 1, 50] = pwr[0, 50] = pwr.max()
    det = pwr > 0.5
    assert det.sum() >= 2 and det[0, 50] and det[REPS - 1, 50]
    # An exact tie keeps both; make row 0 the strict max
    pwr[0, 50] *= 1.001
    out = dsp.nms(det, pwr, a_nsize=3)
    assert out.sum() == 1 and out[0, 50], np.argwhere(out)
    old = det & (pwr == maximum_filter(pwr, size=7, mode="nearest"))
    assert old.sum() == 2  # the pre-change behaviour reports both


def test_nms_interior_unchanged():
    rng = np.random.default_rng(1)
    pwr = rng.random((REPS, 120))
    det = pwr > 0.9
    det[:4] = det[-4:] = False
    pwr[:4] = pwr[-4:] = 0.0  # nothing near the Doppler edges
    old = det & (pwr == maximum_filter(pwr, size=7, mode="nearest"))
    assert np.array_equal(dsp.nms(det, pwr, a_nsize=3), old)


def test_subbin_refine_circular_rows():
    pwr_in = peak_map(0.3, 50, a_row0=60)
    ref, _ = dsp.subbin_refine(pwr_in, [60], [50])
    assert 0.1 < ref[0] < 0.5, ref
    # Same shape (+0.3 rows) centred on row 0, and on row REPS-1
    for row in (0, REPS - 1):
        pwr = peak_map(0.3, 50, a_row0=row)
        off, _ = dsp.subbin_refine(pwr, [row], [50])
        assert np.isclose(off[0], ref[0], rtol=1e-9), (row, off, ref)
    # And a negative offset on row 0 (true peak at row -0.3)
    off, _ = dsp.subbin_refine(peak_map(-0.3, 50, a_row0=0), [0], [50])
    assert -0.5 < off[0] < -0.1, off


def test_subbin_refine_gaussian_exact():
    # A log parabola is exact for a Gaussian (no floor here)
    for off in np.linspace(-0.5, 0.5, 11):
        for row in (0, 60, REPS - 1):
            pwr = peak_map(off, 50.0 + off, a_row0=row, a_floor=0.0)
            ro, co = dsp.subbin_refine(pwr, [row], [50])
            ok = abs(ro[0] - off) < 1e-9 and abs(co[0] - off) < 1e-9
            assert ok, (off, row, ro, co)


def blackman_peak(a_kr, a_kd):
    """Power map of a tone at fractional bins (a_kr, a_kd), process_cpi windows."""
    w = dsp.build_cpi_context(config.RadarConfig(FABRIC_DECHIRP_EN=True)).window_2d
    m, n = np.arange(w.shape[0])[:, None], np.arange(w.shape[1])[None, :]
    x = np.exp(2j * np.pi * (a_kr * n / w.shape[1] + a_kd * m / w.shape[0])) * w
    return np.abs(np.fft.fftshift(np.fft.fft2(x), axes=0)) ** 2


def test_subbin_refine_blackman_accuracy():
    # Measured max error 0.0066 bin (linear-power fit: 0.09); bound 0.01
    worst = 0.0
    for d in np.linspace(-0.5, 0.5, 11):
        for kr, kd in ((100 + d, 10.0), (100.0, 10 + d), (100.0, -64 + d)):
            p = blackman_peak(kr, kd)
            r, c = np.unravel_index(np.argmax(p), p.shape)
            ro, co = dsp.subbin_refine(p, [r], [c])
            row_err = (r - REPS // 2 + ro[0] - kd + REPS / 2) % REPS - REPS / 2
            worst = max(worst, abs(c + co[0] - kr), abs(row_err))
    assert worst < 0.01, worst


def test_subbin_refine_zero_neighbour_finite():
    pwr = np.zeros((REPS, 20))
    pwr[5, 10] = 1.0
    pwr[5, 11] = 0.25
    row, col = dsp.subbin_refine(pwr, [5], [10])
    assert np.isfinite(row[0]) and np.isfinite(col[0])
    assert abs(row[0]) <= 0.5 and abs(col[0]) <= 0.5, (row, col)


def test_subbin_refine_non_max_centre_clipped():
    # Concave in log but the right neighbour is the largest: raw delta 1.5, clipped to 1
    pwr = np.full((REPS, 20), 1e-3)
    pwr[5, 9], pwr[5, 10], pwr[5, 11] = 1e-6, 1e-2, 1.0
    _, col = dsp.subbin_refine(pwr, [5], [10])
    assert col[0] == 1.0, col
    pwr[5, 9], pwr[5, 11] = 1.0, 1e-6
    _, col = dsp.subbin_refine(pwr, [5], [10])
    assert col[0] == -1.0, col


def test_subbin_refine_range_edges_still_skipped():
    pwr = peak_map(0.0, 0, a_shape=(REPS, 50))
    pwr[:, 1] = pwr[:, 0] * 0.5
    _, col = dsp.subbin_refine(pwr, [5, 5], [0, 49])
    assert np.all(col == 0)


def test_wrap_velocity():
    vel = np.fft.fftshift(np.fft.fftfreq(REPS, d=1e-3)) * 0.0259 / 2
    bw = vel[1] - vel[0]
    rows = np.array([10, 40, 77, 100])
    off = np.array([0.3, -0.45, 0.01, 0.25])
    plain = vel[rows] + off * bw
    assert np.array_equal(dsp.wrap_velocity(vel, rows, off), plain)  # bitwise
    # Row 0, -0.3 is below the span and wraps to the top; row REPS-1, +0.4 is
    # still inside it (the last bin is not the span's end), +1.4 wraps to the bottom
    rows = np.array([0, REPS - 1, REPS - 1])
    got = dsp.wrap_velocity(vel, rows, np.array([-0.3, 0.4, 1.4]))
    span = REPS * bw
    assert np.isclose(got[0], vel[0] - 0.3 * bw + span)
    assert got[1] == vel[-1] + 0.4 * bw
    assert np.isclose(got[2], vel[-1] + 1.4 * bw - span)
    assert np.all((got >= vel[0]) & (got < vel[0] + span))
    # Any offset magnitude
    far = dsp.wrap_velocity(vel, np.array([5]), np.array([3.0 * REPS + 0.5]))
    assert np.isclose(far[0], vel[5] + 0.5 * bw) and vel[0] <= far[0] < vel[0] + span


def test_dilate_doppler():
    rng = np.random.default_rng(2)
    for n in (1, 2):
        for _ in range(20):
            m = rng.random((REPS, 40)) > 0.97
            got = dsp.dilate_doppler(m, n)
            # Reference: roll-based cross dilation, range edges border 0
            ref = m.copy()
            for _ in range(n):
                nxt = ref | np.roll(ref, 1, 0) | np.roll(ref, -1, 0)
                nxt[:, 1:] |= ref[:, :-1]
                nxt[:, :-1] |= ref[:, 1:]
                ref = nxt
            assert np.array_equal(got, ref)
            inner = m.copy()
            inner[:n] = inner[-n:] = False
            assert np.array_equal(
                dsp.dilate_doppler(inner, n)[n:-n],
                binary_dilation(inner, iterations=n)[n:-n],
            )
    # Edge case explicitly: row 0 reaches REPS-1
    m = np.zeros((REPS, 10), bool)
    m[0, 5] = True
    assert dsp.dilate_doppler(m, 2)[REPS - 2 :, 5].all()


# --- process_cpi on edge-of-span targets ------------------------------------------
def make_cfg(a_triangle, a_tdm):
    return dataclasses.replace(
        config.RadarConfig(),
        FABRIC_DECHIRP_EN=True,
        SDR_LOOPBACK_EN=False,
        TRIANGLE_EN=a_triangle,
        TDM_EN=a_tdm,
    )


def run_cpi(a_cfg, a_vfracs, a_process=dsp.process_cpi):
    """One target per fraction of MAX_VELOCITY in a_vfracs, ranges 150 m apart."""
    ctx = dsp.build_cpi_context(a_cfg)
    vmax = a_cfg.MAX_VELOCITY
    truth = [(100.0 + 150.0 * i, f * vmax) for i, f in enumerate(a_vfracs)]
    ts = TargetSim()
    ts.set_targets(
        [
            {"r0": r, "v0": v, "a0": 0.0, "amp": 5.0, "duration": 1e6}
            for r, v in truth
        ]
    )
    n = ctx.N_if_samples * a_cfg.LEGS_PER_PERIOD * a_cfg.CHIRP_REPS
    rng = np.random.default_rng(0)
    noise = (1e-3 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(
        np.complex64
    )
    if_sig = ts.apply_if(noise, a_cfg, ctx, a_now=ts.fake_targets[0].t_spawn)
    return a_process(if_sig, a_cfg, ctx)[2], truth


def check_edge_targets(a_cfg, a_process=dsp.process_cpi):
    fracs = [-0.99, 0.99, 1.0, 0.3]
    dets, truth = run_cpi(a_cfg, fracs, a_process)
    vmax = a_cfg.MAX_VELOCITY
    span = 2 * vmax
    bin_v = span / a_cfg.CHIRP_REPS
    for r, v in truth:
        near = [d for d in dets if abs(d["r"] - r) < 8.0]
        assert len(near) == 1, (r, v, near)
        dv = (near[0]["v"] - v + vmax) % span - vmax  # circular distance
        assert abs(dv) < 0.3 * bin_v, (r, v, near[0]["v"], dv / bin_v)
    assert len(dets) == len(truth), dets


def test_edge_targets_sawtooth():
    check_edge_targets(make_cfg(False, False))


def test_edge_targets_triangle():
    check_edge_targets(make_cfg(True, False))


def test_edge_targets_tdm():
    check_edge_targets(make_cfg(False, True))


def test_edge_targets_triangle_tdm():
    check_edge_targets(make_cfg(True, True))


def test_velocity_scale_fabric_sawtooth():
    # Fabric /8 T_EFF is 0.07 % below CHIRP_DUR_S: 0.04 bin at 0.9 * vmax (old axis)
    cfg = make_cfg(False, False)
    assert cfg.T_EFF != cfg.CHIRP_DUR_S
    dets, truth = run_cpi(cfg, [0.9])
    r, v = truth[0]
    near = [d for d in dets if abs(d["r"] - r) < 8.0]
    assert len(near) == 1, near
    err = (near[0]["v"] - v) / (2 * cfg.MAX_VELOCITY / cfg.CHIRP_REPS)
    assert abs(err) < 0.02, err


def test_apply_if_matches_full_length_formula():
    # The old full-length synthesis written out, triangle + TDM: beat tone (conjugate
    # on down rows) * Doppler over the whole block * exp(-j*phi) on B rows.
    cfg = make_cfg(True, True)
    ctx = dsp.build_cpi_context(cfg)
    N = ctx.N_if_samples
    rng = np.random.default_rng(3)
    n = N * cfg.LEGS_PER_PERIOD * cfg.CHIRP_REPS
    x = (1e-2 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(
        np.complex64
    )
    specs = [
        (200.0, 30.0, -50.0, 0.3),
        (500.0, -12.0, 30.0, 5.0),
        (90.0, 0.0, 0.0, 1.0),
    ]
    ts = TargetSim()
    ts.set_targets(
        [
            {"r0": r, "v0": v, "theta0": th, "amp": a, "duration": 1e6}
            for r, v, th, a in specs
        ]
    )
    y = ts.apply_if(x, cfg, ctx, a_now=ts.fake_targets[0].t_spawn)

    rms = np.sqrt(np.mean(np.abs(x) ** 2))
    idx = np.arange(n)
    t_fast = (idx % N) / cfg.FS_IF
    t_all = idx / cfg.FS_IF
    row = idx // N
    down = row % 2 == 1
    is_b = (row % cfg.LEGS_PER_PERIOD) >= cfg.LEGS_PER_PERIOD // 2
    S = cfg.CHIRP_BW_HZ / cfg.T_EFF
    lam = config.c / cfg.CHIRP_FC_HZ
    ref = x.astype(np.complex128)
    for r, v, th, a in specs:
        f_b = S * 2 * r / config.c
        tone = np.exp(2j * np.pi * f_b * t_fast)
        tone = np.where(down, tone.conj(), tone)
        dop = np.exp(2j * np.pi * (2 * v / lam) * t_all)
        phi = 2 * np.pi * cfg.TDM_TX_ANT_SEP_M * np.sin(np.radians(th)) / lam
        ref += a * rms * tone * dop * np.where(is_b, np.exp(-1j * phi), 1.0)
    err = np.abs(y - ref).max() / rms
    assert err < 5e-6, err


def test_apply_doppler_shift_lengths():
    cfg = config.RadarConfig()
    rng = np.random.default_rng(4)
    for n in (0, 1, 2, 1023, 1024, 1025, 5000):
        x = np.exp(1j * rng.uniform(0, 6, n)).astype(np.complex64)
        fd = 2 * 37.3 * cfg.CHIRP_FC_HZ / config.c
        ref = x * np.exp(-2j * np.pi * fd * np.arange(n) / cfg.FS)
        y = dsp.apply_doppler_shift(x, 37.3, cfg)
        assert y.dtype == x.dtype and y.shape == x.shape
        assert np.abs(y - ref).max(initial=0) < 1e-6, n


def test_apply_noise_statistics():
    rng = np.random.default_rng(5)
    x = np.exp(1j * rng.uniform(0, 6, 400_000)).astype(np.complex64)
    y = dsp.apply_noise(x, 10.0, np.random.default_rng(1))
    assert y.dtype == np.complex64
    noise = y - x
    snr = 10 * np.log10(np.mean(np.abs(x) ** 2) / np.mean(np.abs(noise) ** 2))
    assert abs(snr - 10.0) < 0.05, snr
    assert abs(noise.mean()) < 3e-3
    assert abs(noise.real.var() / noise.imag.var() - 1) < 0.02
    assert abs(np.corrcoef(noise.real, noise.imag)[0, 1]) < 0.01
    again = dsp.apply_noise(x, 10.0, np.random.default_rng(1))
    other = dsp.apply_noise(x, 10.0, np.random.default_rng(2))
    assert np.array_equal(y, again) and not np.array_equal(y, other)
    assert dsp.apply_noise(x, 0.0, np.random.default_rng(1)) is x


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
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
