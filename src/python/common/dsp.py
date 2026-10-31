import numpy as np
from scipy.ndimage import correlate1d
from scipy.ndimage import maximum_filter
from scipy.ndimage import binary_dilation
from scipy.signal import stft
from dataclasses import dataclass

from .config import RadarConfig, c


# ===================================================================================
def generate_chirp(a_config: RadarConfig):
    n = int(round(a_config.CHIRP_DUR_S * a_config.FS))
    t = np.arange(n) / a_config.FS
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
    # TDM: B repeats A (every target at 0 deg)
    return np.tile(A=chirp, reps=a_config.CHIRP_REPS * (2 if a_config.TDM_EN else 1))


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
def frame_sync_circ(a_rx, a_config, a_ctx):
    """
    Rotate a raw RX capture so sample 0 is a chirp boundary.
    Works for purely simulated testing due to the circular nature of the simulation. For real hardware, use frame_sync_linear().
    """
    ref = a_ctx.tx_chirp
    P = len(ref)
    m_offset = estimate_chirp_offset(a_rx=a_rx, a_ref_period=ref)
    return np.roll(a_rx, -m_offset)[: a_config.CHIRP_REPS * P]


def frame_sync_linear(a_rx, a_config, a_ctx, a_delay_samples=0):
    """
    Rotate a raw RX capture so sample 0 is a chirp boundary.
    Hardware Rx captures are linear, so the circ frame sync would put garbage at the beginning of the return
    """
    ref = a_ctx.tx_chirp
    P = len(ref)
    n_cpi = a_config.CHIRP_REPS * P
    m_offset = estimate_chirp_offset(a_rx=a_rx, a_ref_period=ref)
    start = (m_offset - a_delay_samples) % P
    return a_rx[start : start + n_cpi]


# ===================================================================================
def nms(a_detections: np.ndarray, a_rd_matrix_pwr: np.ndarray, a_nsize=2):
    """
    NMS = Non-maximal Suppression. Doppler (axis 0) is circular, range is not.
    """
    local_max = maximum_filter(
        input=a_rd_matrix_pwr, size=2 * a_nsize + 1, mode=("wrap", "nearest")
    )

    return a_detections & (a_rd_matrix_pwr == local_max)


# ===================================================================================
def wrap_rad(a_x: np.ndarray) -> np.ndarray:
    """Wrap angles to [-pi, pi]"""
    return (a_x + np.pi) % (2 * np.pi) - np.pi


# ===================================================================================
def _log_parabola_offset(a_ym, a_y0, a_yp):
    """
    Parabola vertex through three LINEAR power samples, fitted on their logs
    (Gaussian interpolation). Returns 0 where the log parabola is not concave-down,
    and clips to [-1, 1] to stay inside the three-sample support.
    """
    tiny = np.finfo(np.float64).tiny
    lm, l0, lp = (
        np.log(np.maximum(np.asarray(y, np.float64), tiny)) for y in (a_ym, a_y0, a_yp)
    )
    d = lm + lp - 2 * l0
    valid = d < 0
    # The inner np.where avoids a divide warning; invalid results are discarded.
    delta = np.where(valid, 0.5 * (lm - lp) / np.where(valid, d, 1), 0)
    return np.clip(delta, -1, 1)


# ===================================================================================
def subbin_refine(a_pwr, a_rows, a_cols):
    """
    Parabolic sub-bin interpolation around detected peaks.
    Returns (row_offsets, col_offsets) as fractional bin displacements.

    For each detection at (row, col), fits a parabola through the log of the
    three power samples on each axis (exact for a Gaussian peak, ~0.01 bin
    error with the Blackman window vs ~0.085 on linear power):
        delta = 0.5 * (l_left - l_right) / (l_left + l_right - 2*l_center)
    The offset is clipped to [-1, 1]; at a local max it is within +-0.5.
    Doppler rows are circular; range columns skip the edge (no neighbour).
    """
    n_rows, n_cols = a_pwr.shape
    rows, cols = np.asarray(a_rows), np.asarray(a_cols)
    # One offset slot per detection; both arrays share length because
    # (rows[i], cols[i]) are paired coordinates for detection i.
    # Default value of 0 means "no shift" for range-edge detections.
    row_offsets = np.zeros(len(rows), dtype=float)
    col_offsets = np.zeros(len(rows), dtype=float)

    # --- Range axis (columns) ---
    # Skip detections at the left/right edge since no neighbor on that side
    col_mask = (cols > 0) & (cols < n_cols - 1)
    if col_mask.any():
        r, c = rows[col_mask], cols[col_mask]
        # Left, center and right neighbor power for each detection
        col_offsets[col_mask] = _log_parabola_offset(
            a_pwr[r, c - 1], a_pwr[r, c], a_pwr[r, c + 1]
        )

    # --- Doppler axis (rows) --- same logic vertically, neighbours wrap around
    if len(rows):
        row_offsets[:] = _log_parabola_offset(
            a_pwr[(rows - 1) % n_rows, cols],
            a_pwr[rows, cols],
            a_pwr[(rows + 1) % n_rows, cols],
        )

    return row_offsets, col_offsets


# ===================================================================================
def tdm_phasor(
    a_config: RadarConfig,
    a_XA: np.ndarray,
    a_XB: np.ndarray,
    a_rss: np.ndarray,
    a_rows: np.ndarray,
    a_cols: np.ndarray,
    a_conj: bool = False,
):
    """
    Unit phasor exp(j*psi) per detection: the TDM A-B phase, Doppler gap removed.
    a_rows: fftshifted Doppler rows of these maps; a_cols index the same slice as
    a_XA, a_XB, a_rss. a_conj: maps built from conj(rows), i.e. the down ramps.
    """

    # Phase diff
    dphi = np.angle(a_XB[a_rows, a_cols] * np.conj(a_XA[a_rows, a_cols]))
    # Doppler correction
    k_d = (a_rows - a_config.CHIRP_REPS // 2).astype(float)
    y_m, y_0, y_p = np.log(
        a_rss[
            [
                (a_rows - 1) % a_config.CHIRP_REPS,
                a_rows,
                (a_rows + 1) % a_config.CHIRP_REPS,
            ],
            a_cols,
        ]
        + 1e-12
    )
    # Only a concave-down fit is a peak (a noise cell at low SNR may not be)
    den = y_m + y_p - 2 * y_0
    k_d += np.where(den < 0, 0.5 * (y_m - y_p) / np.where(den < 0, den, 1), 0)
    k_d = (k_d + a_config.CHIRP_REPS / 2) % a_config.CHIRP_REPS - (
        a_config.CHIRP_REPS / 2
    )
    psi = dphi - np.pi * k_d / a_config.CHIRP_REPS
    # A conj-built map carries the conjugate phase. Read on its own rows, its
    # Doppler is mirrored too, so the gap term above already holds.
    return np.exp(-1j * psi) if a_conj else np.exp(1j * psi)


# ===================================================================================
def estimate_angle(a_config: RadarConfig, a_phasor: np.ndarray):
    """
    Angle (deg) per detection from tdm_phasor(), or from a sum of them (circular
    mean, e.g. up + down ramp). psi0 is removed here.
    """
    psi = wrap_rad(np.angle(a_phasor) - np.radians(a_config.ANGLE_PSI0_DEG))
    # Degrees, like FakeTarget.theta0
    theta = np.degrees(
        np.arcsin(
            np.clip(
                -psi
                * c
                / (2 * np.pi * a_config.CHIRP_FC_HZ * a_config.TDM_TX_ANT_SEP_M),
                -1,
                1,
            )
        )
    )
    return theta


# ===================================================================================
def target_dicts(a_kind, a_r, a_v, a_theta=None):
    """Target dicts for one kind; theta only when given (TDM)."""
    if a_theta is None:
        return [{"r": r, "v": v, "kind": a_kind} for r, v in zip(a_r, a_v)]
    return [
        {"r": r, "v": v, "theta": t, "kind": a_kind}
        for r, v, t in zip(a_r, a_v, a_theta)
    ]


# ===================================================================================
def box_sum(a_x: np.ndarray, a_size: int) -> np.ndarray:
    """
    Sum over the a_size x a_size cells centred on each cell. Doppler (axis 0)
    wraps, range (axis 1) edges are "nearest".
    Two 1-D passes in float64, each output summed straight from its window (no
    running sum), so a strong peak's rounding error stays inside its window.
    """
    ones = np.ones(a_size)
    rows = correlate1d(a_x, ones, axis=0, mode="wrap", output=np.float64)
    return correlate1d(rows, ones, axis=1, mode="nearest", output=np.float64)


# ===================================================================================
def dilate_doppler(a_mask: np.ndarray, a_iterations: int) -> np.ndarray:
    """binary_dilation with the Doppler axis (0) circular; range edges stay border 0."""
    n = a_iterations
    padded = np.pad(a_mask, ((n, n), (0, 0)), mode="wrap")
    return binary_dilation(padded, iterations=n)[n : n + a_mask.shape[0]]


# ===================================================================================
def wrap_velocity(a_vel: np.ndarray, a_rows, a_row_off) -> np.ndarray:
    """
    Refined velocity of Doppler rows plus a fractional row offset, wrapped back
    into the velocity axis span. In-span values equal vel[rows] + off * bin width.
    """
    bw = a_vel[1] - a_vel[0]
    v = a_vel[a_rows] + a_row_off * bw
    lo = a_vel[0]
    span = len(a_vel) * bw
    out = (v < lo) | (v >= lo + span)
    if out.any():
        v = np.where(out, lo + (v - lo) % span, v)
    return v


# ===================================================================================
def cfar_ca_2d(
    a_rd_matrix: np.ndarray,
    a_config: RadarConfig,
    a_apply_nms: bool = True,
):

    # Training cells = outer box minus the guard box (the CUT sits in the guard)
    outer = 1 + 2 * a_config.CFAR_GUARD_LEN + 2 * a_config.CFAR_TRAINING_LEN
    guard = 1 + 2 * a_config.CFAR_GUARD_LEN
    n_training = outer**2 - guard**2

    # Get real power
    rd_matrix_power = np.abs(a_rd_matrix) ** 2
    # Mean of the training cells. The subtraction must stay in float64 (in float32
    # it cancels next to a strong target); cast back like the convolve it replaced.
    noise_level = (
        (box_sum(rd_matrix_power, outer) - box_sum(rd_matrix_power, guard)) / n_training
    ).astype(rd_matrix_power.dtype)

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
def iq_from_raw(a_raw: np.ndarray) -> np.ndarray:
    """
    Raw interleaved int16 RX block -> normalized complex64 (12-bit ADC in an int16
    word, hence 2^11 - 1). sdr.read_block and offline.replay both use it, so a
    recorded block converts exactly like a live one; it lives here, not in sdr.py,
    so replay runs without libiio.
    """
    raw = a_raw.astype(np.float32) / (2**11 - 1)
    return raw[0::2] + 1j * raw[1::2]


# ===================================================================================
def mix_signal(a_rx_signal: np.ndarray, a_tx_signal: np.ndarray):
    """
    Mix input and output signal
    """
    if_signal = a_tx_signal * np.conj(a_rx_signal)
    return if_signal


# ===================================================================================
@dataclass
class CPIContext:
    tx_chirp: np.ndarray
    tx_seq: np.ndarray
    N_chirp_samples: int
    N_if_samples: int
    fs_if: float
    T_rep: float
    ranges_pos: np.ndarray
    velocities: np.ndarray
    pos: np.ndarray
    window_2d: np.ndarray


def build_cpi_context(a_config: RadarConfig) -> CPIContext:
    tx_chirp = generate_chirp(a_config=a_config)
    tx_seq = generate_chirp_sequence(a_config=a_config)
    # N_chirp_samples is the TX/ADC-rate chirp length (unaffected by fabric
    # decimation - that only shrinks what leaves the board). N_if_samples/fs_if
    # are what the RD map is actually built from: equal to N_chirp_samples/FS
    # in software mode (I5 guide Section 6), FS/8 and SWEEP_LEN/8 in fabric
    # decimated mode.
    N_chirp_samples = int(round(a_config.CHIRP_DUR_S * a_config.FS))
    N_if_samples = a_config.N_IF
    fs_if = a_config.FS_IF
    T_rep = a_config.LEGS_PER_PERIOD * a_config.CHIRP_DUR_S

    range_freqs = np.fft.fftfreq(N_if_samples, d=1 / fs_if)
    doppler_freqs = np.fft.fftshift(np.fft.fftfreq(a_config.CHIRP_REPS, d=T_rep))
    # Slope S = B/T_EFF, not B/CHIRP_DUR_S: SWEEP_LEN (and so T_EFF) shrinks by
    # up to 7 samples under decimation, and B is what must stay exact.
    ranges = range_freqs * c / (2 * a_config.CHIRP_BW_HZ / a_config.T_EFF)
    velocities = doppler_freqs * c / (2 * a_config.CHIRP_FC_HZ)

    pos = (ranges >= 0) & (ranges <= a_config.MAX_RANGE)

    range_window = np.blackman(N_if_samples)
    doppler_window = np.blackman(a_config.CHIRP_REPS)
    window_2d = doppler_window[:, np.newaxis] * range_window[np.newaxis, :]

    return CPIContext(
        tx_chirp,
        tx_seq,
        N_chirp_samples,
        N_if_samples,
        fs_if,
        T_rep,
        ranges[pos],
        velocities,
        pos,
        window_2d,
    )


# ===================================================================================
def align_down_doppler(a_map: np.ndarray):
    # Align a down-chirp RD map onto the up-chirp Doppler convention
    return np.roll(np.flipud(a_map), shift=1, axis=0)


# ===================================================================================
def process_cpi(a_if_signal: np.ndarray, a_config: RadarConfig, a_ctx: CPIContext):
    """
    Coherent processing interval:
    1. Reshape IF into [N_reps x N_chirp_samples]
    2. DC offset removal
    3. Stationary clutter removal / MTI
    4. Apply windows
    5. Generate RD map
    6. Remove Tx leakage
    7. Generate detections
    8. Point-cloud reduction
    9. Sub-bin refinement
    10. Angle estimation (TDM_EN)
    11. Target readout
    """
    N = a_ctx.N_if_samples
    pos = a_ctx.pos
    rng = a_ctx.ranges_pos
    vel = a_ctx.velocities

    # ---------------------------------------------
    # 1. Reshape IF into [N_reps x N_chirp_samples]
    # ---------------------------------------------
    L = a_config.LEGS_PER_PERIOD
    full_matrix_iq = a_if_signal[: L * a_config.CHIRP_REPS * N].reshape(
        a_config.CHIRP_REPS, L, N
    )

    if a_config.TDM_EN:
        # A chirps are the first half of each period; B rows are the second half.
        full_matrix_iq_A = full_matrix_iq[:, : L // 2].reshape(-1, N)
        full_matrix_iq_B = full_matrix_iq[:, L // 2 : L].reshape(-1, N)
    else:
        full_matrix_iq_A = full_matrix_iq.reshape(-1, N)

    if a_config.TRIANGLE_EN:
        up_matrix_iq = full_matrix_iq_A[0::2, :].copy()
        down_matrix_iq = full_matrix_iq_A[1::2, :].copy()
        if a_config.TDM_EN:
            up_matrix_iq_B = full_matrix_iq_B[0::2, :].copy()
            down_matrix_iq_B = full_matrix_iq_B[1::2, :].copy()
    else:
        up_matrix_iq = full_matrix_iq_A.copy()
        if a_config.TDM_EN:
            up_matrix_iq_B = full_matrix_iq_B.copy()

    # ---------------------------------------------
    # 2. DC offset removal
    # ---------------------------------------------
    # LO self-mixing residuals, ADC offset bullscheiss
    up_matrix_iq -= up_matrix_iq.mean(axis=1, keepdims=True)
    if a_config.TRIANGLE_EN:
        down_matrix_iq -= down_matrix_iq.mean(axis=1, keepdims=True)
    if a_config.TDM_EN:
        up_matrix_iq_B -= up_matrix_iq_B.mean(axis=1, keepdims=True)
        if a_config.TRIANGLE_EN:
            down_matrix_iq_B -= down_matrix_iq_B.mean(axis=1, keepdims=True)

    # ---------------------------------------------
    # 3. Stationary Clutter Removal / MTI
    # ---------------------------------------------
    # Same as fast-time DC removal, a 0 Hz Doppler is a constant appearing down all columns
    # Remove found constant from all range bins :-)
    if a_config.MTI_EN:
        up_matrix_iq -= up_matrix_iq.mean(axis=0, keepdims=True)
        if a_config.TRIANGLE_EN:
            down_matrix_iq -= down_matrix_iq.mean(axis=0, keepdims=True)
        if a_config.TDM_EN:
            up_matrix_iq_B -= up_matrix_iq_B.mean(axis=0, keepdims=True)
            if a_config.TRIANGLE_EN:
                down_matrix_iq_B -= down_matrix_iq_B.mean(axis=0, keepdims=True)

    # ---------------------------------------------
    # 4. Apply windows
    # ---------------------------------------------
    up_matrix_iq *= a_ctx.window_2d
    if a_config.TRIANGLE_EN:
        down_matrix_iq *= a_ctx.window_2d
    if a_config.TDM_EN:
        up_matrix_iq_B *= a_ctx.window_2d
        if a_config.TRIANGLE_EN:
            down_matrix_iq_B *= a_ctx.window_2d

    # ---------------------------------------------
    # 5. Generate RD map
    # ---------------------------------------------
    up_matrix_rd = np.fft.fftshift(np.fft.fft2(up_matrix_iq), axes=0)
    if a_config.TRIANGLE_EN:
        down_matrix_rd = np.fft.fftshift(np.fft.fft2(np.conj(down_matrix_iq)), axes=0)
    if a_config.TDM_EN:
        up_matrix_rd_B = np.fft.fftshift(np.fft.fft2(up_matrix_iq_B), axes=0)
        if a_config.TRIANGLE_EN:
            down_matrix_rd_B = np.fft.fftshift(
                np.fft.fft2(np.conj(down_matrix_iq_B)), axes=0
            )

    # ---------------------------------------------
    # 6. Remove Tx leakage
    # ---------------------------------------------
    # Tx leakage has analog group delay and multipath propagation.
    # Thus bin 0-N will be useless for detection so zero them to not affect CFAR
    up_matrix_rd_median = np.median(np.abs(up_matrix_rd))
    up_matrix_rd[:, : a_config.CFAR_MASK_N] = up_matrix_rd_median
    if a_config.TRIANGLE_EN:
        down_matrix_rd_median = np.median(np.abs(down_matrix_rd))
        down_matrix_rd[:, : a_config.CFAR_MASK_N] = down_matrix_rd_median
    if a_config.TDM_EN:
        up_matrix_rd_B[:, : a_config.CFAR_MASK_N] = np.median(np.abs(up_matrix_rd_B))
        if a_config.TRIANGLE_EN:
            down_matrix_rd_B[:, : a_config.CFAR_MASK_N] = np.median(
                np.abs(down_matrix_rd_B)
            )

    # Root-sum-square of A and B for CFAR and the display (A only without TDM).
    # The complex maps stay untouched for the angle.
    if a_config.TDM_EN:
        up_rss = np.sqrt(np.abs(up_matrix_rd) ** 2 + np.abs(up_matrix_rd_B) ** 2)
        if a_config.TRIANGLE_EN:
            down_rss = np.sqrt(
                np.abs(down_matrix_rd) ** 2 + np.abs(down_matrix_rd_B) ** 2
            )
    else:
        up_rss = np.abs(up_matrix_rd)
        if a_config.TRIANGLE_EN:
            down_rss = np.abs(down_matrix_rd)

    magnitude_db_rd_up = 20 * np.log10(up_rss[:, pos] + 1e-12)
    if a_config.TRIANGLE_EN:
        magnitude_db_rd_down = 20 * np.log10(down_rss[:, pos] + 1e-12)

    # Normalize
    max_val = np.max(magnitude_db_rd_up)
    if a_config.TRIANGLE_EN:
        max_val_2 = np.max(magnitude_db_rd_down)
        max_val = max(max_val, max_val_2)
        magnitude_db_rd_down -= max_val
    magnitude_db_rd_up -= max_val

    # ---------------------------------------------
    # 7. Generate detections
    # ---------------------------------------------
    if a_config.TRIANGLE_EN:
        # NMS each ramp independently so every target is a single clean peak before pairing
        up_detections, up_pwr = cfar_ca_2d(
            a_rd_matrix=up_rss, a_config=a_config, a_apply_nms=True
        )
        down_detections, down_pwr = cfar_ca_2d(
            a_rd_matrix=down_rss, a_config=a_config, a_apply_nms=True
        )
    else:
        up_detections, up_pwr = cfar_ca_2d(
            a_rd_matrix=up_rss, a_config=a_config, a_apply_nms=True
        )

    # ---------------------------------------------
    # 8. Perform point-cloud reduction on combined detections
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
        both_mask = up_mask_full & dilate_doppler(down_mask_full, PAIR_TOL)
        both_dil = dilate_doppler(both_mask, PAIR_TOL)
        up_mask = up_mask_full & ~both_dil
        down_mask = down_mask_full & ~both_dil

    # ---------------------------------------------
    # 9. Sub-bin refinement
    # 10. Angle estimation (TDM_EN)
    # 11. Target readout
    # ---------------------------------------------
    rng_bw = rng[1] - rng[0]

    if a_config.TDM_EN:
        # Same positive-range slice as the masks
        X_A = up_matrix_rd[:, pos]
        X_B = up_matrix_rd_B[:, pos]
        rss = up_rss[:, pos]

    theta_both = theta_up = theta_down = theta = None
    if a_config.TRIANGLE_EN:
        both_dop, both_rng = np.where(both_mask)
        up_only_dop, up_only_rng = np.where(up_mask)
        down_only_dop, down_only_rng = np.where(down_mask)

        # Refine "both" detections using the average power of up and down chirp.
        # col_off[i] and row_off[i] are fractional bin shifts for detection i.
        # Multiplying by bin width converts the shift to physical units.
        row_off, col_off = subbin_refine(avg_pwr, both_dop, both_rng)
        both_rng_r = rng[both_rng] + col_off * rng_bw
        both_dop_r = wrap_velocity(vel, both_dop, row_off)

        # Refine up-only detections using the up-chirp power (range-masked)
        row_off, col_off = subbin_refine(up_pwr_pos, up_only_dop, up_only_rng)
        up_rng_r = rng[up_only_rng] + col_off * rng_bw
        up_dop_r = wrap_velocity(vel, up_only_dop, row_off)

        # Refine down-only detections using the Doppler-aligned down-chirp power.
        row_off, col_off = subbin_refine(down_pwr_pos, down_only_dop, down_only_rng)
        down_rng_r = rng[down_only_rng] + col_off * rng_bw
        down_dop_r = wrap_velocity(vel, down_only_dop, row_off)

        if a_config.TDM_EN:
            X_A_down = down_matrix_rd[:, pos]
            X_B_down = down_matrix_rd_B[:, pos]
            rss_down = down_rss[:, pos]
            # The masks are on the up map's Doppler rows; the down maps are read on
            # their own. align_down_doppler's row map, i -> (REPS - i) % REPS, is
            # its own inverse.
            both_dop_down = (a_config.CHIRP_REPS - both_dop) % a_config.CHIRP_REPS
            down_only_dop_raw = (
                a_config.CHIRP_REPS - down_only_dop
            ) % a_config.CHIRP_REPS

            # "both": up + down phasors, two independent looks (sigma / sqrt(2))
            theta_both = estimate_angle(
                a_config,
                tdm_phasor(a_config, X_A, X_B, rss, both_dop, both_rng)
                + tdm_phasor(
                    a_config,
                    X_A_down,
                    X_B_down,
                    rss_down,
                    both_dop_down,
                    both_rng,
                    a_conj=True,
                ),
            )
            theta_up = estimate_angle(
                a_config,
                tdm_phasor(a_config, X_A, X_B, rss, up_only_dop, up_only_rng),
            )
            theta_down = estimate_angle(
                a_config,
                tdm_phasor(
                    a_config,
                    X_A_down,
                    X_B_down,
                    rss_down,
                    down_only_dop_raw,
                    down_only_rng,
                    a_conj=True,
                ),
            )

        targets = (
            target_dicts("both", both_rng_r, both_dop_r, theta_both)
            + target_dicts("up", up_rng_r, up_dop_r, theta_up)
            + target_dicts("down", down_rng_r, down_dop_r, theta_down)
        )
    else:
        up_det_dop, up_det_rng = np.where(up_detections[:, pos])

        row_off, col_off = subbin_refine(up_pwr[:, pos], up_det_dop, up_det_rng)
        up_rng_r = rng[up_det_rng] + col_off * rng_bw
        up_dop_r = wrap_velocity(vel, up_det_dop, row_off)

        if a_config.TDM_EN:
            theta = estimate_angle(
                a_config,
                tdm_phasor(a_config, X_A, X_B, rss, up_det_dop, up_det_rng),
            )

        targets = target_dicts("up", up_rng_r, up_dop_r, theta)

    down_db = magnitude_db_rd_down if a_config.TRIANGLE_EN else None
    return magnitude_db_rd_up, down_db, targets, rng, vel


# ===================================================================================
def apply_doppler_shift(
    a_signal: np.ndarray, a_velocity: float, a_config: RadarConfig, a_fs: float = None
):
    """
    Simulate Doppler shift on a signal sampled at a_fs (default a_config.FS,
    the raw-RX rate). Pass a_config.FS_IF when shifting a signal already in
    the decimated IF domain (target_sim.apply_if) - the phasor's time base
    must match the signal's actual sample spacing, not the ADC rate.
    """
    fs = a_fs if a_fs is not None else a_config.FS
    # Doppler shift = 2*v/c * f_c
    doppler_hz = 2 * a_velocity * a_config.CHIRP_FC_HZ / c
    t = np.arange(len(a_signal)) / fs
    # Phase computed in float64 then cast so complex64 captures stay complex64
    phasor = np.exp(-2j * np.pi * doppler_hz * t).astype(a_signal.dtype, copy=False)
    return a_signal * phasor


# ===================================================================================
def apply_noise(a_signal: np.ndarray, a_snr_db: float):
    """
    Simulate additive white Gaussian noise on a signal
    """
    if a_snr_db <= 0:
        return a_signal

    sig_rms = np.sqrt(np.mean(np.abs(a_signal) ** 2))
    noise_std = sig_rms / (10 ** (a_snr_db / 20)) / np.sqrt(2)  # Split across I and Q
    noise = noise_std * (
        np.random.randn(*a_signal.shape) + 1j * np.random.randn(*a_signal.shape)
    )
    return a_signal + noise.astype(a_signal.dtype, copy=False)


# ===================================================================================
def inst_freq(a_signal: np.ndarray, a_radar_config: RadarConfig):
    # derivative of unwrapped phase -> Hz
    return np.diff(np.unwrap(np.angle(a_signal))) * a_radar_config.FS / (2 * np.pi)


# ===================================================================================
def spectrogram(a_signal: np.ndarray, a_config: RadarConfig, a_nperseg=256, a_fs=None):
    fs = a_fs if a_fs is not None else a_config.FS
    f, t, S = stft(x=a_signal, fs=fs, nperseg=a_nperseg, return_onesided=False)
    S = np.fft.fftshift(S, axes=0)
    f = np.fft.fftshift(f)
    S_db = 20 * np.log10(np.abs(S.T) + 1e-12)  # shape: (n_time, NFFT)
    S_db -= S_db.max()
    return S_db, t, f


# ===================================================================================

if __name__ == "__main__":
    print("Hello world!")
