#!/usr/bin/env python3
"""
2T1R TDM angle exercise (Part K10 step 1, Phase 6 guide Section 13.4).

Standalone numpy model of one target at (r, v, theta) seen by two switched TX
antennas and one RX. Deliberately does NOT use process_cpi or apply_if: it is
the independent reference that K10 steps 2-4 are checked against.

Signal model (IF domain), rows m = 0..2*REPS-1, even = A, odd = B, row m
starts at m*T:

    x[m, n] = a * exp(j*(2*pi*f_b*n/FS_IF + 2*pi*f_s*m*T))
                * (B ? exp(-j*2*pi*d*sin(theta)/lam) : 1) + noise
    f_s = 2*v/lam   (same sign as process_cpi's velocity axis)

Experiments (plots go to scripts/angle/local/, git-ignored):

  v        angle error vs v at theta in {-30, 0, +30}, with and without the
           Doppler term. Expect ~8.9 deg at 0 and 20 m/s without it.
  subbin   integer k_d vs sub-bin k_d, target half a Doppler bin off.
  snr      sigma_theta vs per-antenna cell SNR (Monte Carlo) vs
           1 / (sqrt(SNR) * pi * cos(theta)).
  iso      optional: finite switch isolation leaks A into B rows and back.

Usage (venv active):
  python scripts/angle/tdm_exercise.py --exp v
  python scripts/angle/tdm_exercise.py --exp all --trials 500
"""

import sys
import os
import argparse

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "python"))

from common import config, dsp

OUT_DIR = os.path.join(os.path.dirname(__file__), "local")

D_M = 0.0259  # TX column centre spacing (ANGLE_D_M), not exactly lam/2
R_M = 99.0  # target range
SEED = 0


# ===================================================================================
def wrap_rad(a_x: np.ndarray) -> np.ndarray:
    """Wrap angles to [-pi, pi]"""
    return (a_x + np.pi) % (2 * np.pi) - np.pi


# ===================================================================================
def make_config() -> config.RadarConfig:
    # Fabric /8 + TDM: N_IF 707, FS_IF 7.075 MSPS, MAX_VELOCITY 64.6 m/s
    return config.RadarConfig(TDM_EN=True, FABRIC_DECHIRP_EN=True)


# ===================================================================================
def make_if(
    a_cfg: config.RadarConfig,
    a_theta_deg: float,
    a_v: float,
    a_snr_db: float = None,
    a_iso_db: float = None,
    a_rng: np.random.Generator = None,
) -> np.ndarray:
    """
    IF matrix [2*CHIRP_REPS x N_IF], rows alternating A, B.
    a_snr_db: per-sample input SNR, None = no noise.
    a_iso_db: switch isolation, None = ideal switch.
    """

    # 1. Generate beat tone at f_b = 2*R_M*BW/(c*T_EFF) (same for A and B)
    f_b = a_cfg.CHIRP_BW_HZ * 2 * R_M / (config.c * a_cfg.T_EFF)
    n = np.arange(a_cfg.N_IF)
    m = np.arange(2 * a_cfg.CHIRP_REPS)[:, np.newaxis]  # Column vector for rows
    # Fast-time phase, shape (N_IF,)
    phase = 2 * np.pi * f_b * n / a_cfg.FS_IF

    # 2. Generate slow-time phase at f_s = 2*v/lam (same for A and B)
    lam = config.c / a_cfg.CHIRP_FC_HZ
    f_s = 2 * a_v / lam
    # Slow-time phase, shape (2*CHIRP_REPS, 1)
    slow_time_phase = 2 * np.pi * f_s * m * a_cfg.T_EFF
    # (rows, 1) + (N_IF,) broadcasts to the full (2*CHIRP_REPS, N_IF) matrix
    x = np.exp(1j * (phase + slow_time_phase))

    # 3. Generate B-row angle phase at phase = 2*pi*d*sin(theta)/lam (only for B rows)
    theta_rad = np.radians(a_theta_deg)
    ang_phase = 2 * np.pi * D_M * np.sin(theta_rad) / lam
    # Add angle phase only to B rows (odd indices)
    x[1::2, :] *= np.exp(-1j * ang_phase)

    # 4. Add isolation leak from A to B and back if a_iso_db is not None
    if a_iso_db is not None:
        iso_linear = 10 ** (-a_iso_db / 20)  # Convert dB to linear scale
        # The off antenna also radiates into active...
        # ... borrow other row and shift by slow-time phase to 'now'
        step = np.exp(1j * 2 * np.pi * f_s * a_cfg.T_EFF)
        temp_A = x[0::2, :].copy()
        temp_B = x[1::2, :].copy()
        x[0::2, :] += iso_linear * temp_B / step
        x[1::2, :] += iso_linear * temp_A * step

    # 5. Add noise if a_snr_db is not None
    if a_snr_db is not None:
        if a_rng is None:
            a_rng = np.random.default_rng()
        # Calculate noise power based on SNR
        signal_power = 1.0  # unit-amplitude tone
        snr_linear = 10 ** (a_snr_db / 10)
        noise_power = signal_power / snr_linear
        noise_std = np.sqrt(noise_power / 2)  # For complex noise
        noise = a_rng.normal(0, noise_std, x.shape) + 1j * a_rng.normal(
            0, noise_std, x.shape
        )
        x += noise
    return x


# ===================================================================================
def estimate_theta(
    a_cfg: config.RadarConfig,
    a_x: np.ndarray,
    a_doppler_term: bool = True,
    a_subbin: bool = True,
) -> float:
    """
    Split A/B, window + 2D FFT each, peak on |XA|^2 + |XB|^2, then
    dphi -> psi -> theta (TODO.md K10 step 3). Returns theta in degrees.
    """
    xa = a_x[0::2, :]
    xb = a_x[1::2, :]
    window = np.outer(np.blackman(a_cfg.CHIRP_REPS), np.blackman(a_cfg.N_IF))
    # fftshift on the Doppler axis only: signed Doppler bin, range stays 0..N_IF-1
    XA = np.fft.fftshift(np.fft.fft2(xa * window), axes=0)
    XB = np.fft.fftshift(np.fft.fft2(xb * window), axes=0)
    pwr = np.abs(XA) ** 2 + np.abs(XB) ** 2

    # Positive beats only: the first half of the range axis
    half = pwr[:, : a_cfg.N_IF // 2]
    row, col = np.unravel_index(np.argmax(half), half.shape)
    dphi = np.angle(XB[row, col] * np.conj(XA[row, col]))

    k_d = 0.0
    if a_doppler_term:
        k_d = row - a_cfg.CHIRP_REPS // 2
        if a_subbin:
            # Parabola on log power: a windowed peak is closest to a parabola there.
            # Neighbours wrap with % REPS: the Doppler axis is circular.
            n = a_cfg.CHIRP_REPS
            y_m, y_0, y_p = np.log(pwr[[(row - 1) % n, row, (row + 1) % n], col])
            k_d += 0.5 * (y_m - y_p) / (y_m + y_p - 2 * y_0)

    # Motion-corrected phase: one A-to-B gap of Doppler is pi*k_d/REPS
    psi = wrap_rad(dphi - np.pi * k_d / a_cfg.CHIRP_REPS)
    lam = config.c / a_cfg.CHIRP_FC_HZ
    # Minus: IF = TX * conj(RX) conjugates the echo phase
    theta_rad = np.arcsin(np.clip(-psi * lam / (2 * np.pi * D_M), -1.0, 1.0))
    return np.degrees(theta_rad)


# ===================================================================================
def exp_error_vs_v(a_cfg: config.RadarConfig):
    thetas = [-30, 0, 30]
    v = np.linspace(-1.5 * a_cfg.MAX_VELOCITY, 1.5 * a_cfg.MAX_VELOCITY, 1201)
    # One colour per theta: dots = Doppler term on, dashed = off
    plt.figure(figsize=(10, 6))
    for i, theta in enumerate(thetas):
        err_with = []
        err_without = []
        for v_i in v:
            x = make_if(a_cfg, theta, v_i)
            est_with = estimate_theta(a_cfg, x, a_doppler_term=True)
            est_without = estimate_theta(a_cfg, x, a_doppler_term=False)
            err_with.append(est_with - theta)
            err_without.append(est_without - theta)
        err_with = np.array(err_with)
        # Dots, not a line: past the alias theta 0 flips between +-87 deg per point
        plt.plot(
            v, err_with, ".", ms=2, color=f"C{i}", label=f"{theta:+d} deg, term on"
        )
        plt.plot(v, err_without, "--", color=f"C{i}", label=f"{theta:+d} deg, term off")
        ok = np.abs(err_with) < 1
        print(
            f"theta {theta:+d}: term holds {v[ok].min():+.2f} .. {v[ok].max():+.2f} m/s"
        )
    for s in (-1, 1):
        plt.axvline(s * a_cfg.MAX_VELOCITY, color="k", ls=":", lw=1)
    print(f"MAX_VELOCITY {a_cfg.MAX_VELOCITY:.2f} m/s")
    plt.xlabel("Target Velocity (m/s)")
    plt.ylabel("Angle Error (deg)")
    plt.title("Angle Error vs Target Velocity (dotted lines: +-MAX_VELOCITY)")
    plt.grid()
    plt.legend(fontsize="small", loc="upper center", ncol=3)
    plt.savefig(os.path.join(OUT_DIR, "exp_angle_error_vs_v.png"))
    plt.close()


# ===================================================================================
def exp_subbin(a_cfg: config.RadarConfig):
    # TODO: v half a Doppler bin off, a_subbin True vs False
    # Next: exp_subbin
    # Do: fix θ ∈ {0, 30}. Put the target near Doppler bin 20 and sweep the fractional offset from 0 to 1 bin (~41 points). Plot the angle error with integer k_d and with sub-bin k_d.
    # - Build v from the bin: v = (20 + frac) * lam / (4 * T_EFF * CHIRP_REPS). Use T_EFF so that "half a bin" really is half a bin.

    # Predict first:
    # 1. Integer k_d: what shape does the error have vs the fraction? Where is the jump, and what is the max error at θ = 0 and at 30°? (You already measured one point: 0.22° at half a bin.)
    # 2. Sub-bin on log power: what order of magnitude is the max error?
    # 3. The fraction you can't see: a range off-bin doesn't affect the angle. Why does a Doppler off-bin matter for the angle?

    # Finish line: one plot, and the printed max |error| per curve next to your predictions.
    raise NotImplementedError


# ===================================================================================
def exp_sigma_vs_snr(a_cfg: config.RadarConfig, a_trials: int):
    # TODO: target on-bin in range and Doppler, theta in {0, 30, 60}, cell SNR
    # sweep, std of theta over a_trials vs the formula.
    raise NotImplementedError


# ===================================================================================
def exp_isolation(a_cfg: config.RadarConfig):
    # TODO (optional): bias vs theta at a few isolations, 30 dB = K7 measured
    raise NotImplementedError


# ===================================================================================
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--exp", choices=["v", "subbin", "snr", "iso", "all"], default="all"
    )
    parser.add_argument(
        "--trials", type=int, default=300, help="Monte Carlo runs per point"
    )
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    cfg = make_config()

    if args.exp in ("v", "all"):
        exp_error_vs_v(cfg)
    if args.exp in ("subbin", "all"):
        exp_subbin(cfg)
    if args.exp in ("snr", "all"):
        exp_sigma_vs_snr(cfg, args.trials)
    if args.exp in ("iso", "all"):
        exp_isolation(cfg)


if __name__ == "__main__":
    main()
