import numpy as np
from scipy.ndimage import convolve
from scipy.ndimage import maximum_filter
from scipy.ndimage import binary_dilation
from dataclasses import dataclass

from .config import RadarConfig, c


# ===================================================================================
def generate_chirp(a_config: RadarConfig):
    t = np.arange(0, a_config.CHIRP_DUR_S, 1 / a_config.FS)
    # Baseband up-chirp: instantaneous freq sweeps -B/2 -> +B/2
    phase_up = (
        -0.5 * a_config.CHIRP_BW_HZ * t
        + 0.5 * (a_config.CHIRP_BW_HZ / a_config.CHIRP_DUR_S) * t**2
    )
    up_chirp = np.exp(2 * np.pi * 1j * phase_up)

    if not a_config.TRIANGLE_EN:
        return up_chirp

    # Phase-continuous down-chirp: instantaneous freq sweeps +B/2 -> -B/2
    # Offset by phase at end of up-chirp (0.5*B*T) to avoid discontinuity.
    phase_down = (
        0.5 * a_config.CHIRP_BW_HZ * t
        - 0.5 * (a_config.CHIRP_BW_HZ / a_config.CHIRP_DUR_S) * t**2
    )
    down_chirp = np.exp(2 * np.pi * 1j * phase_down)
    return np.concatenate([up_chirp, down_chirp])


# ===================================================================================
def generate_chirp_sequence(a_config: RadarConfig):
    """
    Generate a sequence of FMCW chirps
    """
    chirp = generate_chirp(a_config=a_config)
    return np.tile(A=chirp, reps=a_config.CHIRP_REPS)


# ===================================================================================
def estimate_chirp_offset(a_rx, a_ref_period, a_n_periods=8):
    """
    Recover the integer sample offset m in [0, P) of the first chirp boundary in
    a raw Rx capture where P = len(a_ref_period)

    On hardware the cyclic TX free-runs and a single RX refill() lands at an arbitrary
    point in the loop so the Rx starts mid-chirp at unknown offset m. Circular
    cross-correlation against one reference period recovers m. The peak is dominated by
    the TX self-leakage (i.e. 0 range)
    """
    # Guard a_n_periods against len(a_rx) // P
    P = len(a_ref_period)
    n_periods = min(a_n_periods, len(a_rx) // P)
    # Precompute conj(fft(a_ref_period)) once.
    conj_fft_ref = np.conj(np.fft.fft(a_ref_period))
    # Circular correlation
    R_k_abs = np.zeros(P)
    for k in range(n_periods):
        segment = a_rx[k * P : (k + 1) * P]
        R_k = np.fft.ifft(np.fft.fft(segment) * conj_fft_ref)
        R_k_abs += np.abs(R_k)
    # Find max correlation index (i.e. the time lag)
    return int(np.argmax(R_k_abs))


# ===================================================================================
def frame_sync(a_rx, a_config, a_ctx):
    """
    Rotate a raw RX capture so sample 0 is a chirp boundary
    """
    ref = a_ctx.tx_chirp
    P = len(ref)
    m_offset = estimate_chirp_offset(a_rx=a_rx, a_ref_period=ref)
    return np.roll(a_rx, -m_offset)[: a_config.CHIRP_REPS * P]


# ===================================================================================
def nms(a_detections: np.ndarray, a_rd_matrix_pwr: np.ndarray, a_nsize=2):
    """
    NMS = Non-maximal Suppression
    """
    local_max = maximum_filter(
        input=a_rd_matrix_pwr, size=2 * a_nsize + 1, mode="nearest"
    )

    return a_detections & (a_rd_matrix_pwr == local_max)


# ===================================================================================
def subbin_refine(a_pwr, a_rows, a_cols):
    """
    Parabolic sub-bin interpolation around detected peaks.
    Returns (row_offsets, col_offsets) as fractional bin displacements.

    For each detection at (row, col), fits a parabola through the three
    power samples on each axis and finds the analytical peak offset:
        delta = 0.5 * (y_left - y_right) / (y_left + y_right - 2*y_center)
    This shifts each detection from its integer bin center toward the true
    peak, typically reducing position error from +-0.5 bins to +-0.05 bins.
    """
    n_rows, n_cols = a_pwr.shape
    rows, cols = np.asarray(a_rows), np.asarray(a_cols)
    # One offset slot per detection; both arrays share length because
    # (rows[i], cols[i]) are paired coordinates for detection i.
    # Default value of 0 means "no shift" for edge detections.
    row_offsets = np.zeros(len(rows), dtype=float)
    col_offsets = np.zeros(len(rows), dtype=float)

    # --- Range axis (columns) ---
    # Skip detections at the left/right edge since no neighbor on that side
    col_mask = (cols > 0) & (cols < n_cols - 1)
    if col_mask.any():
        r, c = rows[col_mask], cols[col_mask]
        # Grab left neighbor, center, and right neighbor power for each detection
        ym, y0, yp = a_pwr[r, c - 1], a_pwr[r, c], a_pwr[r, c + 1]
        # Denominator of the parabola formula; negative means the parabola
        # opens downward e.g. local maxima
        d = ym + yp - 2 * y0
        valid = d < 0
        # Apply formula only where the parabola is concave-down (valid peak)
        # The inner np.where replaces invalid d values with 1 to avoid a
        # divide-by-zero warning since those results are discarded by the outer np.where.
        col_offsets[col_mask] = np.where(
            valid, 0.5 * (ym - yp) / np.where(valid, d, 1), 0
        )

    # --- Doppler axis (rows) --- same logic applied vertically
    # Skip detections at the top/bottom edge
    row_mask = (rows > 0) & (rows < n_rows - 1)
    if row_mask.any():
        r, c = rows[row_mask], cols[row_mask]
        # Grab upper neighbor, center, and lower neighbor power
        ym, y0, yp = a_pwr[r - 1, c], a_pwr[r, c], a_pwr[r + 1, c]
        d = ym + yp - 2 * y0
        valid = d < 0
        row_offsets[row_mask] = np.where(
            valid, 0.5 * (ym - yp) / np.where(valid, d, 1), 0
        )

    return row_offsets, col_offsets


# ===================================================================================
def cfar_ca_2d(
    a_rd_matrix: np.ndarray,
    a_config: RadarConfig,
    a_apply_nms: bool = True,
):

    # Implement kernel
    dim_1d = 1 + 2 * a_config.CFAR_GUARD_LEN + 2 * a_config.CFAR_TRAINING_LEN
    n_guard = (2 * a_config.CFAR_GUARD_LEN + 1) ** 2
    n_training = dim_1d**2 - n_guard
    cfar_kernel_2d = np.ones((dim_1d, dim_1d), dtype=float) / n_training
    cfar_kernel_2d[
        a_config.CFAR_TRAINING_LEN : a_config.CFAR_TRAINING_LEN
        + (2 * a_config.CFAR_GUARD_LEN)
        + 1,
        a_config.CFAR_TRAINING_LEN : a_config.CFAR_TRAINING_LEN
        + (2 * a_config.CFAR_GUARD_LEN)
        + 1,
    ] = 0.0

    # Get real power
    rd_matrix_power = np.abs(a_rd_matrix) ** 2
    # Convolve
    noise_level = convolve(
        input=rd_matrix_power, weights=cfar_kernel_2d, mode="nearest"
    )

    # Threshold scale factor: T=a*P
    alpha = n_training * (a_config.CFAR_PFA ** (-1 / n_training) - 1)
    threshold = noise_level * alpha

    detected = rd_matrix_power > threshold

    if a_apply_nms:
        detected = nms(
            a_detections=detected, a_rd_matrix_pwr=rd_matrix_power, a_nsize=3
        )

    return detected, rd_matrix_power


# ===================================================================================
def mix_signal(a_rx_signal: np.ndarray, a_tx_signal: np.ndarray):
    """
    Mix input and output signal
    """
    # import matplotlib.pyplot as plt

    # test = RadarConfig()
    # return a_tx_signal * np.conj(a_rx_signal)
    if_signal = a_tx_signal * np.conj(a_rx_signal)
    # fig, (ax1, ax2, ax3) = plt.subplots(nrows=3, sharex=True)
    # # Plot signal using spectrogram
    # ax1.specgram(a_tx_signal, NFFT=256, Fs=test.FS)
    # ax2.specgram(a_rx_signal, NFFT=256, Fs=test.FS)
    # ax3.specgram(if_signal, NFFT=256, Fs=test.FS)
    # ax1.set_ylim(-test.CHIRP_BW_HZ / 2 - 1e3, test.CHIRP_BW_HZ / 2 + 1e3)
    # ax2.set_ylim(-test.CHIRP_BW_HZ / 2 - 1e3, test.CHIRP_BW_HZ / 2 + 1e3)
    # plt.show()
    return if_signal


# ===================================================================================
@dataclass
class CPIContext:
    tx_chirp: np.ndarray
    N_chirp_samples: int
    T_rep: float
    ranges_pos: np.ndarray
    velocities: np.ndarray
    pos: np.ndarray
    window_2d: np.ndarray


def build_cpi_context(a_config: RadarConfig) -> CPIContext:
    tx_chirp = generate_chirp(a_config=a_config)
    N_chirp_samples = len(np.arange(0, a_config.CHIRP_DUR_S, 1 / a_config.FS))
    T_rep = 2 * a_config.CHIRP_DUR_S if a_config.TRIANGLE_EN else a_config.CHIRP_DUR_S

    range_freqs = np.fft.fftfreq(N_chirp_samples, d=1 / a_config.FS)
    doppler_freqs = np.fft.fftshift(np.fft.fftfreq(a_config.CHIRP_REPS, d=T_rep))
    ranges = range_freqs * c / (2 * a_config.CHIRP_BW_HZ / a_config.CHIRP_DUR_S)
    velocities = doppler_freqs * c / (2 * a_config.CHIRP_FC_HZ)

    pos = (ranges >= 0) & (
        ranges <= a_config.OP_RANGE_FACTOR * (c * a_config.CHIRP_DUR_S) / 2
    )

    range_window = np.blackman(N_chirp_samples)
    doppler_window = np.blackman(a_config.CHIRP_REPS)
    window_2d = doppler_window[:, np.newaxis] * range_window[np.newaxis, :]

    return CPIContext(
        tx_chirp, N_chirp_samples, T_rep, ranges[pos], velocities, pos, window_2d
    )


# ===================================================================================
def align_down_doppler(a_map: np.ndarray):
    # Align a down-chirp RD map onto the up-chirp Doppler convention
    return np.roll(np.flipud(a_map), shift=1, axis=0)


# ===================================================================================
def process_cpi(a_if_signal: np.ndarray, a_config: RadarConfig, a_ctx: CPIContext):
    """
    Coherent processing interval:
    1. Create 2D structure
    2. Perform range+Doppler FFTs
    3. Perform CFAR detection
    4. Clustering
    """
    N = a_ctx.N_chirp_samples
    pos = a_ctx.pos
    rng = a_ctx.ranges_pos
    vel = a_ctx.velocities

    # ---------------------------------------------
    # 1. Reshape IF into [N_reps x N_chirp_samples]
    # ---------------------------------------------
    if a_config.TRIANGLE_EN:
        full_matrix_iq = a_if_signal[: 2 * a_config.CHIRP_REPS * N].reshape(
            2 * a_config.CHIRP_REPS, N
        )
        up_matrix_iq = full_matrix_iq[0::2, :].copy()
        down_matrix_iq = full_matrix_iq[1::2, :].copy()
    else:
        up_matrix_iq = (
            a_if_signal[: a_config.CHIRP_REPS * N]
            .reshape(a_config.CHIRP_REPS, N)
            .copy()
        )

    # ---------------------------------------------
    # 2. Fast-time DC / TX-leakage removal
    # ---------------------------------------------
    # TX self-leakage dechirps to a constant across each chirp's fast-time samples
    # i.e. a DC term landing in range bin 0. Subtract each chirp's mean (per row) to remove it.
    up_matrix_iq -= up_matrix_iq.mean(axis=1, keepdims=True)
    if a_config.TRIANGLE_EN:
        down_matrix_iq -= down_matrix_iq.mean(axis=1, keepdims=True)

    # ---------------------------------------------
    # 3. Apply windows
    # ---------------------------------------------
    up_matrix_iq *= a_ctx.window_2d
    if a_config.TRIANGLE_EN:
        down_matrix_iq *= a_ctx.window_2d

    # ---------------------------------------------
    # 4. Generate RD map
    # ---------------------------------------------
    up_matrix_rd = np.fft.fftshift(np.fft.fft2(up_matrix_iq), axes=0)
    magnitude_db_rd_up = 20 * np.log10(np.abs(up_matrix_rd[:, pos]) + 1e-12)
    if a_config.TRIANGLE_EN:
        down_matrix_rd = np.fft.fftshift(np.fft.fft2(np.conj(down_matrix_iq)), axes=0)
        magnitude_db_rd_down = 20 * np.log10(np.abs(down_matrix_rd[:, pos]) + 1e-12)

    # ---------------------------------------------
    # 5. Generate detections
    # ---------------------------------------------
    # (TX-leakage DC was removed on the fast-time axis in step 2, so no range-bin-0
    # blanking is needed here.)
    if a_config.TRIANGLE_EN:
        # NMS each ramp independently so every target is a single clean peak before pairing
        up_detections, up_pwr = cfar_ca_2d(
            a_rd_matrix=up_matrix_rd, a_config=a_config, a_apply_nms=True
        )
        down_detections, down_pwr = cfar_ca_2d(
            a_rd_matrix=down_matrix_rd, a_config=a_config, a_apply_nms=True
        )
    else:
        up_detections, up_pwr = cfar_ca_2d(
            a_rd_matrix=up_matrix_rd, a_config=a_config, a_apply_nms=True
        )

    # ---------------------------------------------
    # 6. Perform point-cloud reduction on combined detections
    # ---------------------------------------------
    if a_config.TRIANGLE_EN:
        # Bring the down map onto the up map's Doppler convention, then operate in the
        # positive-range region only.
        up_mask_full = up_detections[:, pos]
        up_pwr_pos = up_pwr[:, pos]
        down_mask_full = align_down_doppler(down_detections)[:, pos]
        down_pwr_pos = align_down_doppler(down_pwr)[:, pos]
        avg_pwr = (up_pwr_pos + down_pwr_pos) / 2

        # A target confirmed in BOTH ramps is "both". Pairing gate is +-PAIR_TOL bins:
        # the up/down peaks coincide for a clean target (range-Doppler coupling k*v is
        # not an issue atm), so the gate only needs to absorb noise jitter of
        # weak targets that wanders the two peaks a bin or two apart. Targets are many
        # bins apart, so a small gate cannot cross-pair distinct targets.
        # Each ramp is already NMS'd to one clean cell per target, so both_mask (a subset
        # of up_mask_full) needs no further thinning. A separate NMS here against avg_pwr
        # would incorrectly drop valid pairings whose up cell is not the avg_pwr local max.
        PAIR_TOL = 2
        both_mask = up_mask_full & binary_dilation(down_mask_full, iterations=PAIR_TOL)
        both_dil = binary_dilation(both_mask, iterations=PAIR_TOL)
        up_mask = up_mask_full & ~both_dil
        down_mask = down_mask_full & ~both_dil

    # ---------------------------------------------
    # 7. Target readout
    # ---------------------------------------------
    rng_bw = rng[1] - rng[0]
    vel_bw = vel[1] - vel[0]

    if a_config.TRIANGLE_EN:
        both_dop, both_rng = np.where(both_mask)
        up_only_dop, up_only_rng = np.where(up_mask)
        down_only_dop, down_only_rng = np.where(down_mask)

        # Refine "both" detections using the average power of up and down chirp.
        # col_off[i] and row_off[i] are fractional bin shifts for detection i.
        # Multiplying by bin width converts the shift to physical units.
        row_off, col_off = subbin_refine(avg_pwr, both_dop, both_rng)
        both_rng_r = rng[both_rng] + col_off * rng_bw
        both_dop_r = vel[both_dop] + row_off * vel_bw

        # Refine up-only detections using the up-chirp power (range-masked)
        row_off, col_off = subbin_refine(up_pwr_pos, up_only_dop, up_only_rng)
        up_rng_r = rng[up_only_rng] + col_off * rng_bw
        up_dop_r = vel[up_only_dop] + row_off * vel_bw

        # Refine down-only detections using the Doppler-aligned down-chirp power.
        row_off, col_off = subbin_refine(down_pwr_pos, down_only_dop, down_only_rng)
        down_rng_r = rng[down_only_rng] + col_off * rng_bw
        down_dop_r = vel[down_only_dop] + row_off * vel_bw

        targets = (
            [{"r": r, "v": v, "kind": "both"} for r, v in zip(both_rng_r, both_dop_r)]
            + [{"r": r, "v": v, "kind": "up"} for r, v in zip(up_rng_r, up_dop_r)]
            + [{"r": r, "v": v, "kind": "down"} for r, v in zip(down_rng_r, down_dop_r)]
        )
    else:
        up_det_dop, up_det_rng = np.where(up_detections[:, pos])

        row_off, col_off = subbin_refine(up_pwr[:, pos], up_det_dop, up_det_rng)
        up_rng_r = rng[up_det_rng] + col_off * rng_bw
        up_dop_r = vel[up_det_dop] + row_off * vel_bw

        targets = [{"r": r, "v": v, "kind": "up"} for r, v in zip(up_rng_r, up_dop_r)]

    down_db = magnitude_db_rd_down if a_config.TRIANGLE_EN else None
    return magnitude_db_rd_up, down_db, targets, rng, vel


# ===================================================================================

if __name__ == "__main__":
    print("Hello world!")
