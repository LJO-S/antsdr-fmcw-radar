import numpy as np
import common.dsp as dsp
import common.gui as gui
import common.config as config
import argparse
import sys
from PySide6.QtWidgets import QApplication

# ===================================================================================
# Psysical constant
k_b = 1.38e-23  # Boltsmann constant
T0 = 290  # K, standard reference temperature


# ===================================================================================
class SoftFMCWModel:
    def __init__(self, a_config: config.RadarConfig):
        self.config = a_config

    def add_amplitude(self, a_signal, a_rcs, a_range):
        # Standard radar equation: P_r = P_t * G_t * G_r * λ**2 * σ / ((4π)**3 * R**4)
        wavelength = dsp.c / self.config.CHIRP_FC_HZ
        tx_pwr_w = 10 ** ((self.config.TX_PWR_DBM - 30) / 10)
        tx_gain = 10 ** (self.config.TX_GAIN_DB / 10)
        rx_gain = 10 ** (self.config.RX_GAIN_DB / 10)

        rx_pwr = (tx_pwr_w * tx_gain * rx_gain * wavelength**2 * a_rcs) / (
            ((4 * np.pi) ** 3) * a_range**4
        )

        amplitude = np.sqrt(rx_pwr)

        return a_signal * amplitude

    def add_velocity(self, a_signal, a_velocity):
        """
        Add Doppler shift
        """
        # Calculate Doppler shift
        doppler_freq = 2 * a_velocity * self.config.CHIRP_FC_HZ / dsp.c
        # Create signal
        t = np.arange(0, len(a_signal) * (1 / self.config.FS), (1 / self.config.FS))
        doppler_signal = np.exp(-2 * np.pi * 1j * doppler_freq * t)
        # Mix signals
        return a_signal * doppler_signal

    def add_delay(self, a_signal, a_range):
        tau_s = 2 * a_range / dsp.c
        tau_samples = int(np.ceil(tau_s * self.config.FS))
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
            noise_pwr = k_b * T0 * self.config.FS * noise_figure
            # Split equally across I and Q so total power = noise_pwr
            noise_std = np.sqrt(noise_pwr / 2)
            n = len(rx_signal)
            rx_signal += noise_std * (
                np.random.normal(0, 1, n) + 1j * np.random.normal(0, 1, n)
            )

        return rx_signal

    def add_imperfections(self, a_tx_signal, a_rx_signal):
        # TX leakage: direct coupling from TX port to RX port
        leakage = a_tx_signal * 10 ** (-self.config.ISOLATION_DB / 20)
        rx = a_rx_signal + leakage

        # DC offset: constant bias from LO self-mixing / ADC offset
        rx = rx + (self.config.DC_I + 1j * self.config.DC_Q)

        # IQ imbalance: amplitude and phase mismatch between I and Q branches.
        # Q branch is scaled and slightly rotated toward I creating an image at -f
        i = np.real(rx)
        q = np.imag(rx)
        amp = 1.0 + self.config.IQ_AMPL_ERR
        phi = np.deg2rad(self.config.IQ_PHASE_ERR_DEG)
        q_imbalanced = amp * (q * np.cos(phi) + i * np.sin(phi))
        return i + 1j * q_imbalanced

    def run_simulation(self, a_targets, a_noise_figure_db=0):
        # 1. Generate chirp sequence
        tx_signal = dsp.generate_chirp_sequence(a_config=self.config)

        # 2. Simulate echo
        rx_signal = self.simulate_received_signal(
            a_tx_signal=tx_signal,
            a_targets=a_targets,
            a_noise_figure_db=a_noise_figure_db,
        )

        # 3. Add HW imperfections
        rx_signal_realistic = self.add_imperfections(
            a_tx_signal=tx_signal, a_rx_signal=rx_signal
        )

        # 4. Downmix
        if_signal = dsp.mix_signal(
            a_rx_signal=rx_signal_realistic, a_tx_signal=tx_signal
        )

        # 5. Process IF signal
        rd_map_db_up, rd_map_db_down, targets, ranges, velocities = dsp.process_cpi(
            a_if_signal=if_signal,
            a_config=self.config,
            a_ctx=dsp.build_cpi_context(a_config=self.config),
        )

        return (
            rd_map_db_up,
            rd_map_db_down,
            targets,
            ranges,
            velocities,
            tx_signal,
            rx_signal_realistic,
            if_signal,
        )

    def run_test(self):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FMCW radar soft model")
    parser.add_argument("-p", "--plot", action="store_true", help="Show plots")
    args = parser.parse_args()
    # ===============================================================================
    # 1. Fetch config
    radar_config = config.RadarConfig()

    # 2. Create soft FMCW model
    soft_fmcw_model = SoftFMCWModel(a_config=radar_config)

    # 3 .Generate targets
    # R [m]   V [m/s]   σ [m2]
    # targets = [
    #     {"range": 300, "velocity": -50, "rcs": 30.0, "Comment": "House"},
    #     {"range": 600, "velocity": 150, "rcs": 30.0, "Comment": "House"},
    #     {"range": 900, "velocity": -150, "rcs": 30.0, "Comment": "House"},
    #     {"range": 1200, "velocity": 50, "rcs": 30.0, "Comment": "House"},
    #     {"range": 1500, "velocity": -50, "rcs": 30.0, "Comment": "House"},
    #     {"range": 1800, "velocity": 50, "rcs": 30.0, "Comment": "House"},
    #     {"range": 2100, "velocity": -50, "rcs": 30.0, "Comment": "House"},
    #     {"range": 2400, "velocity": 50, "rcs": 30.0, "Comment": "House"},
    #     {"range": 2700, "velocity": -50, "rcs": 30.0, "Comment": "House"},
    # ]

    # TARGET GENERATION
    rng = np.random.default_rng(seed=42)
    r_min = 100  # minimum range for first target [m]
    range_res = config.c / (2 * radar_config.CHIRP_BW_HZ)
    min_spacing = 20 * range_res  # minimum range separation between targets [m]

    targets = []
    r = r_min
    while r < 0.8 * radar_config.MAX_RANGE:
        v = rng.uniform(
            -0.5 * radar_config.MAX_VELOCITY, 0.5 * radar_config.MAX_VELOCITY
        )
        targets.append({"range": float(r), "velocity": float(v), "rcs": 10.0})
        r += min_spacing + rng.uniform(0, 2 * min_spacing)

    print(f"Generated {len(targets)} targets:")
    for t in targets:
        print(f"  range={t['range']:.1f} m  velocity={t['velocity']:.2f} m/s")

    # 4. Run simulation
    (
        rd_map_db_up,
        rd_map_db_down,
        detected_targets,
        ranges,
        velocities,
        tx_signal,
        rx_signal,
        if_signal,
    ) = soft_fmcw_model.run_simulation(a_targets=targets, a_noise_figure_db=5)

    detected_targets.sort(key=lambda t: t["r"])
    for i, target in enumerate(detected_targets):
        if not radar_config.TRIANGLE_EN or (
            radar_config.TRIANGLE_EN and target["kind"] == "both"
        ):
            print(
                f'\nTarget {i}: \t R = {int(np.round(target["r"]))} m \t V = {int(np.round(target["v"]))} m/s'
            )

    # 5. Start GUI
    app = QApplication(sys.argv)
    display = gui.RadarDisplay()
    display.set_detection_limits(
        r_min=0,
        r_max=radar_config.MAX_RANGE,
        v_min=-radar_config.MAX_VELOCITY,
        v_max=radar_config.MAX_VELOCITY,
    )
    display.update(
        a_rd_up_db=rd_map_db_up,
        a_rd_down_db=rd_map_db_down,
        a_targets=detected_targets,
        a_ranges=ranges,
        a_velocities=velocities,
    )

    NFFT = 256
    hop = NFFT // 2

    # Frequency axis: -fs/2 … +fs/2  (two-sided, IQ signal)
    a_f = np.fft.fftshift(np.fft.fftfreq(NFFT, d=1 / radar_config.FS))

    def make_spec(sig):
        w = np.blackman(NFFT)
        n = (len(sig) - NFFT) // hop + 1
        frames = np.array(
            [
                np.fft.fftshift(np.fft.fft(sig[i * hop : i * hop + NFFT] * w))
                for i in range(n)
            ]
        )
        return 20 * np.log10(np.abs(frames) + 1e-12)  # shape: (n_time, NFFT)

    tx_spec = make_spec(tx_signal)
    rx_spec = make_spec(rx_signal)
    if_spec = make_spec(if_signal)

    # Time axis: one value per STFT window
    n_time = tx_spec.shape[0]
    a_t = np.arange(n_time) * hop / radar_config.FS  # seconds

    display.update_signals(tx_spec, rx_spec, if_spec, a_t, a_f)

    display.show()
    app.exec()
