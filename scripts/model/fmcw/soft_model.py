import numpy as np
import matplotlib.pyplot as plt

# ===================================================================================
# Psysical constant
c = 3e8  # Speed of light in m/s
# ===================================================================================
class FMCWModel:
    def __init__(self, a_chirp_fc, a_chirp_bw, a_chirp_duration, a_sampling_rate, a_chirp_reps, plot_en = False, a_triangle_en = False):
        self.chirp_fc = a_chirp_fc
        self.chirp_bw = a_chirp_bw
        self.chirp_duration = a_chirp_duration
        self.fs = a_sampling_rate
        self.chirp_reps = a_chirp_reps
        self.triangle_en = a_triangle_en

        self.chirp = np.zeros(int(self.chirp_duration * self.fs), dtype=complex)
        self.if_signal = np.zeros(int(self.chirp_duration * self.fs), dtype=complex)

        # Print resolved radar chacteristics
        print(f"FMCW Radar Characteristics:")
        print(f"  Chirp Center Frequency: {self.chirp_fc / 1e9} GHz")
        print(f"  Chirp Bandwidth: {self.chirp_bw / 1e6} MHz")
        print(f"  Chirp Duration: {self.chirp_duration * 1e6} us")
        print(f"  Range Resolution: {c / (2 * self.chirp_bw)} m")
        print(f"  Velocity Resolution: {(c/self.chirp_fc) / (2 * self.chirp_duration * self.chirp_reps)} m")
        print(f"  Max Range: {(c * self.chirp_duration) / 2} m")
        print(f"  Max Velocity: {(c/self.chirp_fc) / (4 * self.chirp_duration)} m/s")

        self.plot = plot_en

    def generate_chirp(self, B, T, fs):
        t = np.arange(0, T, 1 / fs)
        # Baseband up-chirp: instantaneous freq sweeps 0 → B
        phase_up = 0.5 * (B / T) * t ** 2
        up_chirp = np.exp(2 * np.pi * 1j * phase_up)

        if not self.triangle_en:
            return up_chirp

        # Phase-continuous down-chirp: instantaneous freq sweeps B → 0.
        # Offset by phase at end of up-chirp (0.5*B*T) to avoid discontinuity.
        phase_down = 0.5 * B * T + B * t - 0.5 * (B / T) * t ** 2
        down_chirp = np.exp(2 * np.pi * 1j * phase_down)
        return np.concatenate([up_chirp, down_chirp])

    def generate_chirp_sequence(self):
        """
        Generate a sequence of FMCW chirps
        """
        self.chirp = self.generate_chirp(self.chirp_bw, self.chirp_duration, self.fs)
        chirp_sequence = np.zeros(self.chirp_reps * len(self.chirp), dtype=complex)
        for i in range(self.chirp_reps):
            chirp_sequence[i * len(self.chirp):(i + 1) * len(self.chirp)] = self.chirp
        return chirp_sequence

    def mix_signals(self, a_tx_signal, a_rx_signal):
        if_signal =  a_tx_signal * np.conj(a_rx_signal)
        fig, (ax1, ax2, ax3) = plt.subplots(nrows=3, sharex=True)
          # Plot signal using spectrogram
        ax1.specgram(a_tx_signal, NFFT=1024, Fs=self.fs)
        ax2.specgram(a_rx_signal, NFFT=1024, Fs=self.fs)
        ax3.specgram(if_signal, NFFT=4096*16, Fs=self.fs)
        ax1.set_ylim(-1e3, self.chirp_bw)
        ax2.set_ylim(-1e3, self.chirp_bw)
        if self.plot:
            plt.show()
        return if_signal
    
    def create_range_doppler_plot(self):
        pass
    
    def estimate_distance(self, a_if_signal, a_fs, a_nbr_targets=1):
        N = len(a_if_signal)
        N_chirp = int(np.ceil(self.chirp_duration * self.fs))

        if self.triangle_en:
            num_chirp = N // (N_chirp * 2)
        else:
            num_chirp = N // N_chirp
        
        freqs = np.fft.fftfreq(N_chirp, d=1 / a_fs)
        estimated_distance = []
        i = 0
        for _ in range(num_chirp):
            if_fft = np.fft.fft(a_if_signal[i * N_chirp: (i+1)*N_chirp] * np.hanning(N_chirp))
            if_fft[freqs < 0.0] = 0.0 
            peak_index = np.argmax(np.abs(if_fft))
            beat_frequency = freqs[peak_index]
            i += 1
            if self.triangle_en:
                if_fft = np.fft.fft(a_if_signal[i * N_chirp: (i+1)*N_chirp] * np.hanning(N_chirp))
                if_fft[freqs > 0.0] = 0.0 
                peak_index = np.argmax(np.abs(if_fft))
                beat_frequency = (beat_frequency - freqs[peak_index]) / 2
                i += 1

            estimated_distance.append((beat_frequency * c) / (2 * (self.chirp_bw / self.chirp_duration)))

        return estimated_distance    
# ===================================================================================
class SoftFMCWModel:
    def __init__(self, fmcw_model, plot_en = False):
        self.fmcw_model = fmcw_model
        self.plot = plot_en

    def simulate_received_signal(self, a_tx_signal, a_target_distance, a_attenuation_db=-3, a_noise_db=0):
        """
        Simulate attenuated return echo
        """
        attenuation_lin = 10.0 ** (a_attenuation_db / 20)
        # To-and-return trip time
        tau_s = 2 * a_target_distance / c
        tau_samples = int(np.ceil(tau_s * self.fmcw_model.fs))
        # Extend time vector
        rx_signal = np.zeros(len(a_tx_signal) + tau_samples, dtype=complex)
        # Insert attenuated original tx
        rx_signal[tau_samples:] = a_tx_signal * attenuation_lin
        # (Optional) Add noise
        if a_noise_db > 0:
            rx_signal += np.random.rand(len(rx_signal)) * (10.0 ** (a_noise_db / 20)) 
        # Zero extend tx_signal to match rx_signal length
        tx_signal = np.zeros(len(rx_signal), dtype=complex)
        tx_signal[:len(a_tx_signal)] = a_tx_signal

        return tx_signal, rx_signal

    def run_simulation(self, a_target_distance):
        # Generate chirp
        tx_signal = self.fmcw_model.generate_chirp_sequence()
        # Simulate echo
        tx_signal, rx_signal = self.simulate_received_signal(a_target_distance=a_target_distance, a_tx_signal=tx_signal)
        if_signal = self.fmcw_model.mix_signals(a_tx_signal=tx_signal, a_rx_signal=rx_signal)
        estimated_distance = self.fmcw_model.estimate_distance(if_signal, self.fmcw_model.fs)
        return estimated_distance
    

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
        a_chirp_bw=CHIRP_BW_HZ  ,
        a_chirp_duration=CHIRP_DURATION_S,
        a_chirp_reps=CHIRP_REPS,
        a_sampling_rate=SAMPLING_RATE_HZ,
        a_triangle_en = TRIANGLE,
        plot_en=PLOT
    )
    # Create soft FMCW model
    soft_fmcw_model = SoftFMCWModel(fmcw_model, plot_en=PLOT)
    # Run simulation
    target_distance = 60  # Target distance in meters
    estimated_distance = soft_fmcw_model.run_simulation(target_distance)
    print(f"Estimated Distance: {np.mean(estimated_distance):.2f} m (True Distance: {target_distance} m)")