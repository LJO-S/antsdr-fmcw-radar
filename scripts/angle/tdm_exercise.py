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

Names:

    theta   target azimuth, deg, > 0 towards antenna B
    phi     geometry phase 2*pi*d*sin(theta)/lam (ang_phase in make_if)
    dphi    measured angle(XB * conj(XA)) at the cell: -phi + Doppler gap
    k_d     signed Doppler bin + sub-bin offset
    psi     dphi - pi*k_d/REPS (the Doppler gap), wrapped: -phi, so
            theta = asin(-psi * lam / (2*pi*d))
    XA, XB  windowed 2D FFTs of the A and B rows

Experiments (plots go to scripts/angle/local/, git-ignored):

  v        angle error vs v at theta in {-30, 0, +30}, with and without the
           Doppler term. Expect ~8.9 deg at 0 and 20 m/s without it.
  subbin   integer k_d vs sub-bin k_d, target half a Doppler bin off.
  snr      sigma_theta vs per-antenna cell SNR (Monte Carlo) vs
           1 / (sqrt(SNR) * pi * cos(theta)).
  iso      finite switch isolation leaks A into B rows and back, swept over the
           leak phase a. First order: phi biased by -2*eps*cos(a)*sin(phi).

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
def window_2d(a_cfg: config.RadarConfig) -> np.ndarray:
    """Per-antenna [CHIRP_REPS x N_IF] Blackman window, as build_cpi_context"""
    return np.outer(np.blackman(a_cfg.CHIRP_REPS), np.blackman(a_cfg.N_IF))


# ===================================================================================
def true_cell(a_cfg: config.RadarConfig, a_v: float) -> tuple:
    """
    (row, col) nearest the target in estimate_theta's map: row on the fftshifted
    Doppler axis (CHIRP_REPS//2 = 0 Hz, circular), col the range bin.
    """
    n = a_cfg.CHIRP_REPS
    lam = config.c / a_cfg.CHIRP_FC_HZ
    # Per-antenna slow time is sampled every 2*T_EFF: bin width 1 / (2*T_EFF*n)
    k = 2 * a_v / lam * 2 * a_cfg.T_EFF * n
    row = (round(k) + n // 2) % n
    # Range bin width is 1 / T_EFF, so f_b * T_EFF = 2*R*BW/c
    col = round(2 * R_M * a_cfg.CHIRP_BW_HZ / config.c)
    return row, col


# ===================================================================================
def add_cell_noise(
    a_cfg: config.RadarConfig,
    a_x: np.ndarray,
    a_cell_snr_db: float,
    a_rng: np.random.Generator = None,
) -> np.ndarray:
    """Copy of a_x plus complex noise giving a_cell_snr_db per antenna in the cell"""
    if a_rng is None:
        a_rng = np.random.default_rng()
    # Cell SNR -> per-sample noise: an on-bin unit tone peaks at sum(w), the
    # noise adds up to var * sum(w^2), so the FFT gains (sum w)^2 / sum(w^2)
    # (~44.8 dB at 128 x 707 Blackman)
    w = window_2d(a_cfg)
    gain = np.sum(w) ** 2 / np.sum(w**2)
    noise_power = gain / 10 ** (a_cell_snr_db / 10)
    noise_std = np.sqrt(noise_power / 2)  # For complex noise
    noise = a_rng.normal(0, noise_std, a_x.shape) + 1j * a_rng.normal(
        0, noise_std, a_x.shape
    )
    return a_x + noise


# ===================================================================================
def make_if(
    a_cfg: config.RadarConfig,
    a_theta_deg: float,
    a_v: float,
    a_cell_snr_db: float = None,
    a_iso_db: float = None,
    a_leak_deg: float = 0.0,
    a_rng: np.random.Generator = None,
) -> np.ndarray:
    """
    IF matrix [2*CHIRP_REPS x N_IF], rows alternating A, B.
    a_cell_snr_db: per-antenna SNR in the target's cell after the windowed 2D FFT
        (on-bin target; off-bin scalloping lowers it), None = no noise.
    a_iso_db: switch isolation under the through path, None = ideal switch.
    a_leak_deg: leak phase vs the through path, the same in both states
        (symmetric switch).
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
        # Linear amplitude and phase of the leak
        leak = 10 ** (-a_iso_db / 20) * np.exp(1j * np.radians(a_leak_deg))
        # The off antenna also radiates into active...
        # ... borrow other row and shift by slow-time phase to 'now'
        step = np.exp(1j * 2 * np.pi * f_s * a_cfg.T_EFF)
        temp_A = x[0::2, :].copy()
        temp_B = x[1::2, :].copy()
        x[0::2, :] += leak * temp_B / step
        x[1::2, :] += leak * temp_A * step

    # 5. Add noise if a_cell_snr_db is not None
    if a_cell_snr_db is not None:
        x = add_cell_noise(a_cfg, x, a_cell_snr_db, a_rng)
    return x


# ===================================================================================
def estimate_theta(
    a_cfg: config.RadarConfig,
    a_x: np.ndarray,
    a_doppler_term: bool = True,
    a_subbin: bool = True,
    a_cell: tuple = None,
) -> float:
    """
    Split A/B, window + 2D FFT each, peak on |XA|^2 + |XB|^2, then
    dphi -> psi -> theta (TODO.md K10 step 3). Returns theta in degrees.
    a_cell: (row, col) to read instead of the peak, e.g. true_cell(). At low SNR
        the peak lands on noise cells, which measures detection, not phase noise.
    """
    xa = a_x[0::2, :]
    xb = a_x[1::2, :]
    window = window_2d(a_cfg)
    # fftshift on the Doppler axis only: signed Doppler bin, range stays 0..N_IF-1
    XA = np.fft.fftshift(np.fft.fft2(xa * window), axes=0)
    XB = np.fft.fftshift(np.fft.fft2(xb * window), axes=0)
    pwr = np.abs(XA) ** 2 + np.abs(XB) ** 2

    if a_cell is None:
        # Positive beats only: the first half of the range  axis
        half = pwr[:, : a_cfg.N_IF // 2]
        row, col = np.unravel_index(np.argmax(half), half.shape)
    else:
        row, col = a_cell
    dphi = np.angle(XB[row, col] * np.conj(XA[row, col]))

    k_d = 0.0
    if a_doppler_term:
        n = a_cfg.CHIRP_REPS
        k_d = row - n // 2
        if a_subbin:
            # Parabola on log power: a windowed peak is closest to a parabola there.
            # Neighbours wrap with % REPS: the Doppler axis is circular.
            y_m, y_0, y_p = np.log(pwr[[(row - 1) % n, row, (row + 1) % n], col])
            # Only a concave-down fit is a peak (a noise cell at low SNR may not be)
            den = y_m + y_p - 2 * y_0
            if den < 0:
                k_d += 0.5 * (y_m - y_p) / den
        # Back into [-n/2, n/2): row 0 is bin -n/2, so a target at +63.8 bins peaks
        # there and the fit gives -64.2. Same slow-time frequency on the 2T grid,
        # but pi*k_d/n below differs by exactly pi between the two
        k_d = (k_d + n / 2) % n - n / 2

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
    thetas = [0, 30]
    doppler_bin = 20
    frac = np.linspace(0, 1, 41)
    # One colour per theta: dots = integer k_d, solid = sub-bin k_d
    plt.figure(figsize=(10, 6))
    for i, theta in enumerate(thetas):
        err_int = []
        err_subbin = []
        for f in frac:
            v_i = (
                (doppler_bin + f)
                * config.c
                / (4 * a_cfg.T_EFF * a_cfg.CHIRP_REPS * a_cfg.CHIRP_FC_HZ)
            )
            x = make_if(a_cfg, theta, v_i)
            est_int = estimate_theta(a_cfg, x, a_doppler_term=True, a_subbin=False)
            est_subbin = estimate_theta(a_cfg, x, a_doppler_term=True, a_subbin=True)
            err_int.append(est_int - theta)
            err_subbin.append(est_subbin - theta)
        err_int = np.array(err_int)
        err_subbin = np.array(err_subbin)
        # Signed error has no log axis: plot |error|, floored so exact zeros show
        floor = 1e-6
        plt.semilogy(
            frac,
            np.maximum(np.abs(err_int), floor),
            ".",
            ms=4,
            color=f"C{i}",
            label=f"{theta:+d} deg, sub-bin off",
        )
        plt.semilogy(
            frac,
            np.maximum(np.abs(err_subbin), floor),
            "-",
            color=f"C{i}",
            label=f"{theta:+d} deg, sub-bin on",
        )
        print(
            f"Theta {theta} deg: max |error| integer k_d {np.max(np.abs(err_int)):.4f} deg"
        )
        print(
            f"Theta {theta} deg: max |error| sub-bin k_d {np.max(np.abs(err_subbin)):.4f} deg"
        )
    plt.xlabel("Fractional Doppler Bin Offset")
    plt.ylabel("|Angle Error| (deg)")
    plt.title(f"Angle Error vs Fractional Doppler Bin Offset")
    plt.grid()
    plt.legend(fontsize="small")
    plt.savefig(os.path.join(OUT_DIR, f"exp_subbin.png"))
    plt.close()


# ===================================================================================
def exp_sigma_vs_snr(a_cfg: config.RadarConfig, a_trials: int):
    thetas = [0, 30, 60]
    snr_db = np.arange(-10, 31, 5)
    rng = np.random.default_rng(SEED)
    lam = config.c / a_cfg.CHIRP_FC_HZ
    # Read the true cell: a peak search at low SNR lands on noise, which measures
    # detection and not phase noise
    cell = true_cell(a_cfg, 0.0)
    plt.figure(figsize=(10, 6))
    for i, theta in enumerate(thetas):
        # Clean signal once per theta, only the noise changes per trial
        x_clean = make_if(a_cfg, theta, 0.0)
        sigma = []
        for snr in snr_db:
            est = np.array(
                [
                    estimate_theta(
                        a_cfg, add_cell_noise(a_cfg, x_clean, snr, rng), a_cell=cell
                    )
                    for _ in range(a_trials)
                ]
            )
            sigma.append(np.std(est))
        sigma = np.array(sigma)
        # Formula: sigma_psi = 1 / sqrt(SNR) (two cells, each 1 / sqrt(2 SNR)),
        # d(psi)/d(theta) = 2*pi*d*cos(theta) / lam. High SNR, no wrap.
        pred = np.degrees(
            1
            / (
                np.sqrt(10 ** (snr_db / 10))
                * 2
                * np.pi
                * D_M
                / lam
                * np.cos(np.radians(theta))
            )
        )
        plt.semilogy(snr_db, sigma, ".", ms=4, color=f"C{i}", label=f"{theta:+d} deg")
        plt.semilogy(snr_db, pred, "-", color=f"C{i}")
        # The formula needs a small phase error and no wrap past +-pi
        ok = snr_db >= 20
        print(
            f"Theta {theta:+d} deg: sigma/formula at >= 20 dB "
            f"{np.round(sigma[ok] / pred[ok], 2)}"
        )
    plt.xlabel("Per-Antenna Cell SNR (dB)")
    plt.ylabel("Std of Angle Estimate (deg)")
    plt.title(
        f"Angle Estimate Std vs Per-Antenna Cell SNR ({a_trials} trials per point)"
    )
    plt.grid()
    plt.legend(fontsize="small")
    plt.savefig(os.path.join(OUT_DIR, "exp_sigma_vs_snr.png"))
    plt.close()


# ===================================================================================
def exp_isolation(a_cfg: config.RadarConfig):
    # Leak e*exp(j*a) in both states: B*conj(A) = exp(-j*phi) + 2*e*cos(a) + O(e^2),
    # so phi is biased by -2*e*cos(a)*sin(phi). Towards boresight at a = 0, away at
    # 180, nothing at +-90. Static target: make_if time-aligns the leak, v changes
    # nothing.
    lam = config.c / a_cfg.CHIRP_FC_HZ
    thetas = np.arange(-60, 61, 2)
    phi = 2 * np.pi * D_M * np.sin(np.radians(thetas)) / lam
    # d(phi)/d(theta): turns a bias in phi into one in theta
    dphi_dtheta = 2 * np.pi * D_M * np.cos(np.radians(thetas)) / lam

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(14, 6))

    # Left: bias vs theta at 30 dB (K7 measured), dots = estimator, lines = first order
    eps = 10 ** (-30 / 20)
    for i, a in enumerate([0, 45, 90, 135, 180]):
        bias = [
            estimate_theta(a_cfg, make_if(a_cfg, th, 0.0, a_iso_db=30, a_leak_deg=a))
            - th
            for th in thetas
        ]
        pred = np.degrees(-2 * eps * np.cos(np.radians(a)) * np.sin(phi) / dphi_dtheta)
        ax0.plot(thetas, bias, ".", color=f"C{i}", label=f"leak {a} deg")
        ax0.plot(thetas, pred, "-", lw=1, color=f"C{i}")
    ax0.set_xlabel("Target Angle (deg)")
    ax0.set_ylabel("Angle Bias (deg)")
    ax0.set_title("Bias vs angle, 30 dB isolation (lines: first order)")
    ax0.grid()
    ax0.legend(fontsize="small")

    # Right: worst |bias| over theta vs leak phase (theta every 6 deg covers the
    # peak at ~36 deg)
    leaks = np.arange(0, 361, 15)
    for i, iso in enumerate((30, 40)):
        eps = 10 ** (-iso / 20)
        worst = [
            max(
                abs(
                    estimate_theta(
                        a_cfg, make_if(a_cfg, th, 0.0, a_iso_db=iso, a_leak_deg=a)
                    )
                    - th
                )
                for th in thetas[::3]
            )
            for a in leaks
        ]
        pred = np.degrees(2 * eps * np.max(np.abs(np.sin(phi)) / dphi_dtheta))
        ax1.plot(leaks, worst, "o", color=f"C{i}", label=f"{iso} dB")
        ax1.plot(leaks, pred * np.abs(np.cos(np.radians(leaks))), "-", color=f"C{i}")
        print(
            f"iso {iso} dB: worst {max(worst):.4f} deg, first order {pred:.4f}, "
            f"guide asin(eps) {np.degrees(np.arcsin(eps)):.4f}"
        )
    ax1.set_xlabel("Leak Phase (deg)")
    ax1.set_ylabel("Worst |Bias| over +-60 deg (deg)")
    ax1.set_title("Worst bias vs leak phase (lines: first order)")
    ax1.grid()
    ax1.legend(fontsize="small")

    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "exp_isolation.png"))
    plt.close(fig)


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
