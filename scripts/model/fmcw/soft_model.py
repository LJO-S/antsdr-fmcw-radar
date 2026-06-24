import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import convolve

# ===================================================================================
# Psysical constant
c = 3e8  # Speed of light in m/s
k_b = 1.38e-23  # Boltsmann constant
T0 = 290  # K, standard reference temperature


# ===================================================================================
class Detector:
    def __init__(self, a_pfa: float, a_guard_len: int, a_training_len: int):
        self.pfa = a_pfa
        self.guard_len = a_guard_len
        self.training_len = a_training_len

    def get_nhood(self, a_rd_matrix, a_xpos, a_ypos, a_axis=None, a_nsize=2):
        if a_axis is None:
            return a_rd_matrix[
                a_ypos - a_nsize : a_ypos + a_nsize + 1,
                a_xpos - a_nsize : a_xpos + a_nsize + 1,
            ]
        elif a_axis == 0:
            return a_rd_matrix[
                a_ypos,
                a_xpos - a_nsize : a_xpos + a_nsize + 1,
            ]
        elif a_axis == 1:
            return a_rd_matrix[
                a_ypos - a_nsize : a_ypos + a_nsize + 1,
                a_xpos,
            ]
        else:
            raise KeyError("What?!")

    def nms(self, a_detections: np.ndarray, a_rd_matrix_pwr: np.ndarray, a_nsize=2):
        """
        NMS = Non-maximal Suppression
        """
        nms_peaks = np.zeros(a_detections.shape, dtype=int)
        for row, col in zip(*np.where(a_detections == True)):
            if np.all(
                a_rd_matrix_pwr[row, col]
                >= self.get_nhood(
                    a_rd_matrix=a_rd_matrix_pwr,
                    a_xpos=col,
                    a_ypos=row,
                    a_axis=None,
                    a_nsize=a_nsize,
                )
            ):
                nms_peaks[row, col] = 1

        return nms_peaks

    def subbin_refine(self, a_pwr, a_rows, a_cols):
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

    def cfar_ca_2d(
        self,
        a_rd_matrix: np.ndarray,
        a_apply_nms: bool = True,
    ):

        # Implement kernel
        dim_1d = 1 + 2 * self.guard_len + 2 * self.training_len
        n_guard = (2 * self.guard_len + 1) ** 2
        n_training = dim_1d**2 - n_guard
        cfar_kernel_2d = np.ones((dim_1d, dim_1d), dtype=float) / n_training
        cfar_kernel_2d[
            self.training_len : self.training_len + (2 * self.guard_len) + 1
        ][self.training_len : self.training_len + (2 * self.guard_len) + 1] = 0.0

        # Get real power
        rd_matrix_power = np.abs(a_rd_matrix) ** 2
        # Convolve
        noise_level = convolve(
            input=rd_matrix_power, weights=cfar_kernel_2d, mode="nearest"
        )

        # Threshold scale factor: T=a*P
        alpha = n_training * (self.pfa ** (-1 / n_training) - 1)
        threshold = noise_level * alpha

        detected = rd_matrix_power > threshold

        if a_apply_nms:
            detected = self.nms(
                a_detections=detected, a_rd_matrix_pwr=rd_matrix_power, a_nsize=3
            )

        return detected, rd_matrix_power


# ===================================================================================
class FMCWModel:
    def __init__(
        self,
        # Radar parameters
        a_chirp_fc,
        a_chirp_bw,
        a_chirp_duration,
        a_sampling_rate,
        a_chirp_reps,
        # Frontend
        a_tx_pwr_dbm,
        a_tx_gain_db,
        a_rx_gain_db,
        # Misc
        a_triangle_en,
        a_operational_range_factor=0.2,
        plot_en=False,
        # Detection parameters
        a_cfar_guard_len=4,
        a_cfar_training_len=10,
        a_cfar_pfa=1e-6,
    ):
        self.chirp_fc = a_chirp_fc
        self.chirp_bw = a_chirp_bw
        self.chirp_duration = a_chirp_duration
        self.fs = a_sampling_rate
        self.chirp_reps = a_chirp_reps
        self.triangle_en = a_triangle_en

        self.op_max_rng_factor = a_operational_range_factor

        self.tx_pwr = 10 ** ((a_tx_pwr_dbm - 30) / 10)
        self.tx_gain = 10 ** (a_tx_gain_db / 10)
        self.rx_gain = 10 ** (a_rx_gain_db / 10)

        self.chirp = np.zeros(int(self.chirp_duration * self.fs), dtype=complex)
        self.if_signal = np.zeros(int(self.chirp_duration * self.fs), dtype=complex)

        self.detector = Detector(
            a_pfa=a_cfar_pfa,
            a_guard_len=a_cfar_guard_len,
            a_training_len=a_cfar_training_len,
        )

        # Print resolved radar chacteristics
        print(f"FMCW Radar Characteristics:")
        print(f"  Chirp Center Frequency: {self.chirp_fc / 1e9} GHz")
        print(f"  Chirp Bandwidth: {self.chirp_bw / 1e6} MHz")
        print(f"  Chirp Duration: {self.chirp_duration * 1e6} us")
        print(f"  Range Resolution: {c / (2 * self.chirp_bw)} m")
        print(
            f"  Velocity Resolution: {(c/self.chirp_fc) / (2 * self.chirp_duration * self.chirp_reps)} m"
        )
        print(f"  Theoretical Max Range: {(c * self.chirp_duration) / 2} m")
        print(
            f"  Operational Max Range: {self.op_max_rng_factor * (c * self.chirp_duration) / 2} m"
        )
        print(f"  Max Velocity: {(c/self.chirp_fc) / (4 * self.chirp_duration)} m/s")
        print(
            f"Processing Gain: {10 * np.log10(self.chirp_duration * self.fs * self.chirp_reps)}"
        )

        self.plot = plot_en

    def generate_chirp(self, B, T, fs):
        t = np.arange(0, T, 1 / fs)
        # Baseband up-chirp: instantaneous freq sweeps -B/2 -> +B/2
        phase_up = -0.5 * B * t + 0.5 * (B / T) * t**2
        up_chirp = np.exp(2 * np.pi * 1j * phase_up)

        if not self.triangle_en:
            return up_chirp

        # Phase-continuous down-chirp: instantaneous freq sweeps +B/2 -> -B/2
        # Offset by phase at end of up-chirp (0.5*B*T) to avoid discontinuity.
        phase_down = 0.5 * B * t - 0.5 * (B / T) * t**2
        down_chirp = np.exp(2 * np.pi * 1j * phase_down)
        return np.concatenate([up_chirp, down_chirp])

    def generate_chirp_sequence(self):
        """
        Generate a sequence of FMCW chirps
        """
        self.chirp = self.generate_chirp(self.chirp_bw, self.chirp_duration, self.fs)
        chirp_sequence = np.zeros(self.chirp_reps * len(self.chirp), dtype=complex)
        for i in range(self.chirp_reps):
            chirp_sequence[i * len(self.chirp) : (i + 1) * len(self.chirp)] = self.chirp
        return chirp_sequence

    def mix_signals(self, a_tx_signal, a_rx_signal):
        if_signal = a_tx_signal * np.conj(a_rx_signal)
        if self.plot:
            fig, (ax1, ax2, ax3) = plt.subplots(nrows=3, sharex=True)
            # Plot signal using spectrogram
            ax1.specgram(a_tx_signal, NFFT=256, Fs=self.fs)
            ax2.specgram(a_rx_signal, NFFT=256, Fs=self.fs)
            ax3.specgram(if_signal, NFFT=256, Fs=self.fs)
            ax1.set_ylim(-self.chirp_bw / 2 - 1e3, self.chirp_bw / 2 + 1e3)
            ax2.set_ylim(-self.chirp_bw / 2 - 1e3, self.chirp_bw / 2 + 1e3)
            plt.show()
        return if_signal

    def create_range_doppler_plot(self, a_if_signal):
        # 1. Reshape IF into [N_reps x N_chirp_samples]
        N_chirp_samples = int(np.ceil(self.chirp_duration * self.fs))

        if self.triangle_en:
            if_signal = a_if_signal[: 2 * self.chirp_reps * N_chirp_samples]
            full_matrix_iq = if_signal.reshape(2 * self.chirp_reps, N_chirp_samples)
            up_matrix_iq = full_matrix_iq[0::2, :]
            down_matrix_iq = full_matrix_iq[1::2, :]
            T_rep = 2 * self.chirp_duration
        else:
            if_signal = a_if_signal[: self.chirp_reps * N_chirp_samples]
            up_matrix_iq = if_signal.reshape(self.chirp_reps, N_chirp_samples)
            T_rep = self.chirp_duration

        range_freqs = np.fft.fftfreq(N_chirp_samples, d=1 / self.fs)
        doppler_freqs = np.fft.fftshift(np.fft.fftfreq(self.chirp_reps, d=T_rep))
        ranges = range_freqs * c / (2 * self.chirp_bw / self.chirp_duration)
        velocities = doppler_freqs * c / (2 * self.chirp_fc)

        # Mask negative range bins (aliases)
        pos = (ranges >= 0) & (
            ranges <= self.op_max_rng_factor * (c * self.chirp_duration) / 2
        )

        # 2. Apply windows
        rows, cols = up_matrix_iq.shape
        range_window = np.blackman(cols)
        doppler_window = np.blackman(rows)

        up_matrix_iq *= range_window[np.newaxis, :] * doppler_window[:, np.newaxis]
        if self.triangle_en:
            down_matrix_iq *= (
                range_window[np.newaxis, :] * doppler_window[:, np.newaxis]
            )

        # 3. Generate RD map
        up_matrix_rd = np.fft.fftshift(np.fft.fft2(up_matrix_iq), axes=0)
        magnitude_db_rd_up = 20 * np.log10(np.abs(up_matrix_rd[:, pos]) + 1e-12)
        if self.triangle_en:
            down_matrix_rd = np.fft.fftshift(
                np.fft.fft2(np.conj(down_matrix_iq)), axes=0
            )
            magnitude_db_rd_down = 20 * np.log10(np.abs(down_matrix_rd[:, pos]) + 1e-12)

        # 4. Generate detections
        if self.triangle_en:
            up_detections, up_pwr = self.detector.cfar_ca_2d(
                a_rd_matrix=up_matrix_rd, a_apply_nms=False
            )
            down_detections, down_pwr = self.detector.cfar_ca_2d(
                a_rd_matrix=down_matrix_rd, a_apply_nms=False
            )
        else:
            up_detections, up_pwr = self.detector.cfar_ca_2d(
                a_rd_matrix=up_matrix_rd, a_apply_nms=True
            )

        # 5. Perform point-cloud reduction on combined detections
        if self.triangle_en:
            up_mask = up_detections[:, pos]
            down_mask = np.flipud(down_detections[:, pos])
            both_mask = up_mask & down_mask
            avg_pwr = (up_pwr[:, pos] + np.flipud(down_pwr)[:, pos]) / 2
            both_mask = self.detector.nms(
                a_detections=both_mask, a_rd_matrix_pwr=avg_pwr, a_nsize=8
            )
            up_mask = up_detections[:, pos] & ~both_mask
            down_mask = np.flipud(down_detections[:, pos]) & ~both_mask

        # 6. Plot and target readout
        if self.triangle_en:
            fig, (ax1, ax2, ax3) = plt.subplots(nrows=3, sharex=True)
            mesh1 = ax1.pcolormesh(
                ranges[pos], velocities, magnitude_db_rd_up, shading="auto", cmap="jet"
            )
            fig.colorbar(mesh1, ax=ax1, label="dB")
            ax1.set_ylabel("Velocity (m/s)")
            ax1.set_title("Up-chirp")
            mesh2 = ax2.pcolormesh(
                ranges[pos],
                np.flip(velocities),
                magnitude_db_rd_down,
                shading="auto",
                cmap="jet",
            )
            fig.colorbar(mesh2, ax=ax2, label="dB")
            ax2.set_ylabel("Velocity (m/s)")
            ax2.set_title("Down-chirp")

            # Extract integer bin indices for each detection category
            up_only_dop, up_only_rng = np.where(up_mask)
            down_only_dop, down_only_rng = np.where(down_mask)
            both_dop, both_rng = np.where(both_mask)

            # Physical width of one bin along each axis, used to convert
            # fractional bin offsets into meters and m/s
            range_bin_width = ranges[pos][1] - ranges[pos][0]
            vel_bin_width = velocities[1] - velocities[0]

            # Refine "both" detections using the average power of up and down chirp.
            # col_off[i] and row_off[i] are fractional bin shifts for detection i.
            # Multiplying by bin width converts the shift to physical units.
            row_off, col_off = self.detector.subbin_refine(avg_pwr, both_dop, both_rng)
            both_rng_r = ranges[pos][both_rng] + col_off * range_bin_width
            both_dop_r = velocities[both_dop] + row_off * vel_bin_width

            # Target readout
            targets = list(zip(both_rng_r, both_dop_r))

            # Refine up-only detections using the up-chirp power (range-masked)
            row_off, col_off = self.detector.subbin_refine(
                up_pwr[:, pos], up_only_dop, up_only_rng
            )
            up_rng_r = ranges[pos][up_only_rng] + col_off * range_bin_width
            up_dop_r = velocities[up_only_dop] + row_off * vel_bin_width

            # Refine down-only detections. The down-chirp power is flipped along
            # the Doppler axis to match the up-chirp coordinate convention before refining.
            row_off, col_off = self.detector.subbin_refine(
                np.flipud(down_pwr)[:, pos], down_only_dop, down_only_rng
            )
            down_rng_r = ranges[pos][down_only_rng] + col_off * range_bin_width
            down_dop_r = velocities[down_only_dop] + row_off * vel_bin_width

            ax3.scatter(up_rng_r, up_dop_r, marker="x", color="white", s=30, label="Up")
            ax3.scatter(
                down_rng_r, down_dop_r, marker="x", color="cyan", s=30, label="Down"
            )
            ax3.scatter(
                both_rng_r, both_dop_r, marker="o", color="yellow", s=50, label="Both"
            )
            ax3.set_facecolor("#0a1628")
            ax3.set_xlim(ranges[pos].min(), ranges[pos].max())
            ax3.set_ylim(velocities.min(), velocities.max())
            ax3.set_xlabel("Range (m)")
            ax3.set_ylabel("Velocity (m/s)")
            ax3.set_title("Detections")
            ax3.legend(loc="upper right")
            ax3.grid(True, color="#1a3a5c", linewidth=0.5)
            cbar3 = fig.colorbar(mesh1, ax=ax3)
            cbar3.ax.set_visible(False)
            fig.suptitle("Range-Doppler Map", fontsize=16, fontweight="bold")
        else:
            fig, (ax1, ax2) = plt.subplots(nrows=2, sharex=True)
            mesh = ax1.pcolormesh(
                ranges[pos], velocities, magnitude_db_rd_up, shading="auto", cmap="jet"
            )
            fig.colorbar(mesh, ax=ax1, label="dB")
            ax1.set_ylabel("Velocity (m/s)")
            ax1.set_title("Range-Doppler Map")

            # Extract integer bin indices for each detection
            up_det_dop, up_det_rng = np.where(up_detections[:, pos])

            # Physical width of one bin along each axis
            range_bin_width = ranges[pos][1] - ranges[pos][0]
            vel_bin_width = velocities[1] - velocities[0]
            # col_off shifts each detection along the range axis (in bins),
            # row_off shifts along the Doppler axis (in bins)
            row_off, col_off = self.detector.subbin_refine(
                up_pwr[:, pos], up_det_dop, up_det_rng
            )

            up_rng_r = ranges[pos][up_det_rng] + col_off * range_bin_width
            up_dop_r = velocities[up_det_dop] + row_off * vel_bin_width

            # Target readout
            targets = list(zip(up_rng_r, up_dop_r))

            ax2.scatter(
                up_rng_r,
                up_dop_r,
                marker="o",
                color="white",
                s=30,
                label="Detections",
            )
            ax2.set_facecolor("#0a1628")
            ax2.set_xlim(ranges[pos].min(), ranges[pos].max())
            ax2.set_ylim(velocities.min(), velocities.max())
            ax2.set_xlabel("Range (m)")
            ax2.set_ylabel("Velocity (m/s)")
            ax2.set_title("Detections")
            ax2.legend(loc="upper right")
            ax2.grid(True, color="#09478A", linewidth=0.5)
            cbar2 = fig.colorbar(mesh, ax=ax2)
            cbar2.ax.set_visible(False)
        if self.plot:
            plt.show()

        return targets


# ===================================================================================
class SoftFMCWModel:
    def __init__(
        self,
        fmcw_model,
        plot_en=False,
        a_isolation_db=40.0,
        a_dc_i=0.0,
        a_dc_q=0.0,
        a_iq_amplitude_err=1e-6,
        a_iq_phase_err_deg=0.1,
    ):
        self.fmcw_model = fmcw_model
        self.isolation_db = a_isolation_db
        self.dc_i = a_dc_i
        self.dc_q = a_dc_q
        self.iq_amplitude_err = a_iq_amplitude_err
        self.iq_phase_err_deg = a_iq_phase_err_deg

        self.plot = plot_en

    def add_amplitude(self, a_signal, a_rcs, a_range):
        # Standard radar equation: P_r = P_t * G_t * G_r * λ**2 * σ / ((4π)**3 * R**4)
        wavelength = c / self.fmcw_model.chirp_fc
        rx_pwr = (
            self.fmcw_model.tx_pwr
            * self.fmcw_model.tx_gain
            * self.fmcw_model.rx_gain
            * wavelength**2
            * a_rcs
        ) / (((4 * np.pi) ** 3) * a_range**4)

        amplitude = np.sqrt(rx_pwr)

        return a_signal * amplitude

    def add_velocity(self, a_signal, a_velocity):
        """
        Add Doppler shift
        """
        # Calculate Doppler shift
        doppler_freq = 2 * a_velocity * self.fmcw_model.chirp_fc / c
        # Create signal
        t = np.arange(
            0, len(a_signal) * (1 / self.fmcw_model.fs), (1 / self.fmcw_model.fs)
        )
        doppler_signal = np.exp(-2 * np.pi * 1j * doppler_freq * t)
        # Mix signals
        return a_signal * doppler_signal

    def add_delay(self, a_signal, a_range):
        tau_s = 2 * a_range / c
        tau_samples = int(np.ceil(tau_s * self.fmcw_model.fs))
        # Extend time vector & insert attenuated original (w Doppler)
        target_rx_signal = np.zeros(len(a_signal), dtype=complex)
        target_rx_signal[tau_samples:] = a_signal[:-tau_samples]
        return target_rx_signal

    def simulate_received_signal(self, a_tx_signal, a_targets, a_noise_figure_db=0):
        """
        Simulate attenuated return echo with physically-grounded thermal noise.

        Noise power: N = k*T*B*F
          k = Boltzmann constant, T = 290 K, B = sampling rate (complex bandwidth),
          F = noise figure (linear). The noise is split equally across I and Q.
        """

        rx_signal = np.zeros(len(a_tx_signal), dtype=complex)
        for target in a_targets:
            # 1. Add Velocity
            rx_signal_doppler = self.add_velocity(
                a_signal=a_tx_signal, a_velocity=target["velocity"]
            )
            # 2. Echo
            rx_signal_echo = self.add_delay(
                a_signal=rx_signal_doppler, a_range=target["range"]
            )
            # 3. Attenuate (radar equation)
            rx_signal_atten = self.add_amplitude(
                a_signal=rx_signal_echo, a_rcs=target["rcs"], a_range=target["range"]
            )
            # 4. Accumulate
            rx_signal += rx_signal_atten

        if a_noise_figure_db > 0:
            noise_figure = 10 ** (a_noise_figure_db / 10)
            # Total noise power referred to receiver input; fs is the complex bandwidth
            noise_pwr = k_b * T0 * self.fmcw_model.fs * noise_figure
            # Split equally across I and Q so total power = noise_pwr
            noise_std = np.sqrt(noise_pwr / 2)
            n = len(rx_signal)
            rx_signal += noise_std * (
                np.random.normal(0, 1, n) + 1j * np.random.normal(0, 1, n)
            )

        return rx_signal

    def add_imperfections(self, a_tx_signal, a_rx_signal):
        # TX leakage: direct coupling from TX port to RX port
        leakage = a_tx_signal * 10 ** (-self.isolation_db / 20)
        rx = a_rx_signal + leakage

        # DC offset: constant bias from LO self-mixing / ADC offset
        rx = rx + (self.dc_i + 1j * self.dc_q)

        # IQ imbalance: amplitude and phase mismatch between I and Q branches.
        # Q branch is scaled and slightly rotated toward I creating an image at -f
        i = np.real(rx)
        q = np.imag(rx)
        amp = 1.0 + self.iq_amplitude_err
        phi = np.deg2rad(self.iq_phase_err_deg)
        q_imbalanced = amp * (q * np.cos(phi) + i * np.sin(phi))
        return i + 1j * q_imbalanced

    def run_simulation(self, a_targets, a_noise_figure_db=0):
        # Generate chirp
        tx_signal = self.fmcw_model.generate_chirp_sequence()
        # Simulate echo
        rx_signal = self.simulate_received_signal(
            a_tx_signal=tx_signal,
            a_targets=a_targets,
            a_noise_figure_db=a_noise_figure_db,
        )

        # Add HW imperfections
        rx_signal_realistic = self.add_imperfections(
            a_tx_signal=tx_signal, a_rx_signal=rx_signal
        )

        # Downmix
        if_signal = self.fmcw_model.mix_signals(
            a_tx_signal=tx_signal, a_rx_signal=rx_signal_realistic
        )

        # Run detection algorithm
        detected_targets = self.fmcw_model.create_range_doppler_plot(
            a_if_signal=if_signal
        )

        return detected_targets


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FMCW radar soft model")
    parser.add_argument("-p", "--plot", action="store_true", help="Show plots")
    parser.add_argument(
        "-t", "--triangle", action="store_true", help="Triangle/sawtooth"
    )
    args = parser.parse_args()

    # ===============================================================================
    # PARAMETERS

    CHIRP_FC_HZ = 900e6
    CHIRP_BW_HZ = 50e6
    CHIRP_DURATION_S = 100e-6
    CHIRP_REPS = 64

    TX_PWR_DBM = 10
    TX_GAIN_DB = 13.0
    RX_GAIN_DB = 14.0

    SAMPLING_RATE_HZ = 56e6
    TRIANGLE = args.triangle

    PLOT = args.plot
    # ===============================================================================
    # Create FMCW model
    fmcw_model = FMCWModel(
        # Chirp params
        a_chirp_fc=CHIRP_FC_HZ,
        a_chirp_bw=CHIRP_BW_HZ,
        a_chirp_duration=CHIRP_DURATION_S,
        a_chirp_reps=CHIRP_REPS,
        # Tx params
        a_tx_pwr_dbm=TX_PWR_DBM,
        a_tx_gain_db=TX_GAIN_DB,
        a_rx_gain_db=RX_GAIN_DB,
        # Misc
        a_sampling_rate=SAMPLING_RATE_HZ,
        a_triangle_en=TRIANGLE,
        plot_en=PLOT,
    )
    # Create soft FMCW model
    soft_fmcw_model = SoftFMCWModel(fmcw_model, plot_en=PLOT)

    # Generate targets
    # R [m]   V [m/s]   σ [m2]
    targets = [
        {"range": 33, "velocity": 0, "rcs": 1.0, "Comment": "Human"},
        {"range": 43, "velocity": 0, "rcs": 1.0, "Comment": "Human"},
        {"range": 53, "velocity": 0, "rcs": 1.0, "Comment": "Human"},
        {"range": 57, "velocity": 0, "rcs": 1.0, "Comment": "Human"},
        {"range": 67, "velocity": 0, "rcs": 1.0, "Comment": "Human"},
        {"range": 70, "velocity": -3, "rcs": 1.0, "Comment": "Human"},
        {"range": 75, "velocity": 17, "rcs": 1.0, "Comment": "Car"},
        {"range": 105, "velocity": 0, "rcs": 32.0, "Comment": "Car"},
        {"range": 800, "velocity": -10, "rcs": 32.0, "Comment": "Car"},
        {"range": 900, "velocity": 0, "rcs": 100.0, "Comment": "House"},
    ]
    # Run simulation
    detected_targets = soft_fmcw_model.run_simulation(
        a_targets=targets, a_noise_figure_db=5
    )

    for i, target in enumerate(detected_targets):
        print(f"\nTarget {i}: \n\t RNG = {target[0]} \n\t VEL = {target[1]}")
