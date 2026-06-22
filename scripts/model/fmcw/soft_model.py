import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import convolve

# ===================================================================================
# TODO
# Target readout
# Accurate amplitude
# ===================================================================================
# Psysical constant
c = 3e8  # Speed of light in m/s


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
        a_triangle_en,
        a_operational_range_factor=0.5,
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

        self.plot = plot_en

    def generate_chirp(self, B, T, fs):
        t = np.arange(0, T, 1 / fs)
        # Baseband up-chirp: instantaneous freq sweeps 0 → B
        phase_up = 0.5 * (B / T) * t**2
        up_chirp = np.exp(2 * np.pi * 1j * phase_up)

        if not self.triangle_en:
            return up_chirp

        # Phase-continuous down-chirp: instantaneous freq sweeps B → 0.
        # Offset by phase at end of up-chirp (0.5*B*T) to avoid discontinuity.
        phase_down = 0.5 * B * T + B * t - 0.5 * (B / T) * t**2
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
        fig, (ax1, ax2, ax3) = plt.subplots(nrows=3, sharex=True)
        # Plot signal using spectrogram
        ax1.specgram(a_tx_signal, NFFT=1024, Fs=self.fs)
        ax2.specgram(a_rx_signal, NFFT=1024, Fs=self.fs)
        ax3.specgram(if_signal, NFFT=1024, Fs=self.fs)
        ax1.set_ylim(-1e3, self.chirp_bw)
        ax2.set_ylim(-1e3, self.chirp_bw)
        if self.plot:
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
            up_detections, _ = self.detector.cfar_ca_2d(
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

        # 6. Plot
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

            up_only_dop, up_only_rng = np.where(up_mask)
            down_only_dop, down_only_rng = np.where(down_mask)
            both_dop, both_rng = np.where(both_mask)
            ax3.scatter(
                ranges[pos][up_only_rng],
                velocities[up_only_dop],
                marker="x",
                color="white",
                s=30,
                label="Up",
            )
            ax3.scatter(
                ranges[pos][down_only_rng],
                velocities[down_only_dop],
                marker="x",
                color="cyan",
                s=30,
                label="Down",
            )
            ax3.scatter(
                ranges[pos][both_rng],
                velocities[both_dop],
                marker="o",
                color="yellow",
                s=50,
                label="Both",
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

            up_det_dop, up_det_rng = np.where(up_detections[:, pos])
            ax2.scatter(
                ranges[pos][up_det_rng],
                velocities[up_det_dop],
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

        # 4. Fetch targets
        # maybe use CFAR here?

    def estimate_velocity(self):
        pass

    def estimate_range(self, a_if_signal, a_fs, a_nbr_targets=1):
        N = len(a_if_signal)
        N_chirp = int(np.ceil(self.chirp_duration * self.fs))

        if self.triangle_en:
            num_chirp = N // (N_chirp * 2)
        else:
            num_chirp = N // N_chirp

        freqs = np.fft.fftfreq(N_chirp, d=1 / a_fs)
        estimated_range = []
        i = 0
        for _ in range(num_chirp):
            if_fft = np.fft.fft(
                a_if_signal[i * N_chirp : (i + 1) * N_chirp] * np.hanning(N_chirp)
            )
            if_fft[freqs < 0.0] = 0.0
            peak_index = np.argmax(np.abs(if_fft))
            beat_frequency = freqs[peak_index]
            i += 1
            if self.triangle_en:
                if_fft = np.fft.fft(
                    a_if_signal[i * N_chirp : (i + 1) * N_chirp] * np.hanning(N_chirp)
                )
                if_fft[freqs > 0.0] = 0.0
                peak_index = np.argmax(np.abs(if_fft))
                beat_frequency = (beat_frequency - freqs[peak_index]) / 2
                i += 1

            estimated_range.append(
                (beat_frequency * c) / (2 * (self.chirp_bw / self.chirp_duration))
            )

        return estimated_range


# ===================================================================================
class SoftFMCWModel:
    def __init__(self, fmcw_model, plot_en=False):
        self.fmcw_model = fmcw_model
        self.plot = plot_en

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
        doppler_signal = np.exp(2 * np.pi * 1j * doppler_freq * t)
        # Mix signals
        return a_signal * doppler_signal

    def add_delay(self, a_signal, a_range):
        tau_s = 2 * a_range / c
        tau_samples = int(np.ceil(tau_s * self.fmcw_model.fs))
        # Extend time vector & insert attenuated original (w Doppler)
        target_rx_signal = np.zeros(len(a_signal), dtype=complex)
        target_rx_signal[tau_samples:] = a_signal[:-tau_samples]
        return target_rx_signal

    def simulate_received_signal(self, a_tx_signal, a_targets, a_noise_db=9):
        """
        Simulate attenuated return echo
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
            rx_signal += rx_signal_echo
        # (Optional) Add noise
        if a_noise_db > 0:
            rx_signal += np.random.normal(0, 1, len(rx_signal)) * (
                10.0 ** (a_noise_db / 20)
            )

        return rx_signal

    def run_simulation(self, a_targets):
        # Generate chirp
        tx_signal = self.fmcw_model.generate_chirp_sequence()
        # Simulate echo
        rx_signal = self.simulate_received_signal(
            a_tx_signal=tx_signal, a_targets=a_targets
        )

        if_signal = self.fmcw_model.mix_signals(
            a_tx_signal=tx_signal, a_rx_signal=rx_signal
        )

        self.fmcw_model.create_range_doppler_plot(a_if_signal=if_signal)

        # Fetch range
        estimated_range = self.fmcw_model.estimate_range(if_signal, self.fmcw_model.fs)

        # Fetch velocity
        estimated_velocity = self.fmcw_model.estimate_velocity(
            if_signal, self.fmcw_model.fs
        )
        return 0, 0


if __name__ == "__main__":
    # ===============================================================================
    # PARAMETERS
    CHIRP_FC_HZ = 900e6
    CHIRP_BW_HZ = 56.6e6
    CHIRP_DURATION_S = 100e-6
    CHIRP_REPS = 128
    SAMPLING_RATE_HZ = 200e6
    TRIANGLE = True

    PLOT = True
    # ===============================================================================
    # Create FMCW model
    fmcw_model = FMCWModel(
        a_chirp_fc=CHIRP_FC_HZ,
        a_chirp_bw=CHIRP_BW_HZ,
        a_chirp_duration=CHIRP_DURATION_S,
        a_chirp_reps=CHIRP_REPS,
        a_sampling_rate=SAMPLING_RATE_HZ,
        a_triangle_en=TRIANGLE,
        plot_en=PLOT,
    )
    # Create soft FMCW model
    soft_fmcw_model = SoftFMCWModel(fmcw_model, plot_en=PLOT)

    # Generate targets
    targets = [
        {
            "range": 2489,  # Target range in meters
            "velocity": -118,  # Target velocite in m/s
            "amplitude": 1.0,
        },
        {
            "range": 105,  # Target range in meters
            "velocity": 0,  # Target velocite in m/s
            "amplitude": 1.0,
        },
        {
            "range": 800,  # Target range in meters
            "velocity": 10,  # Target velocite in m/s
            "amplitude": 1.0,
        },
        {
            "range": 7200,  # Target range in meters
            "velocity": 200,  # Target velocite in m/s
            "amplitude": 1.0,
        },
    ]
    # Run simulation
    estimated_range = soft_fmcw_model.run_simulation(a_targets=targets)
    print(
        # f"Estimated range: {np.mean(estimated_range):.2f} m (True range: {target_range} m)"
    )
