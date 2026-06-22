import numpy as np
import matplotlib.pyplot as plt

# ===================================================================================
# TODO
# Implement Rmax_operational = 70% of Rmax
# Implement CFAR
# Implement multiple targets
# Add noise
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
        ax3.specgram(if_signal, NFFT=1024, Fs=self.fs)
        ax1.set_ylim(-1e3, self.chirp_bw)
        ax2.set_ylim(-1e3, self.chirp_bw)
        if self.plot:
            plt.show()
        return if_signal
    
    def cfar_detection():
        pass
    
    def create_range_doppler_plot(self, a_if_signal):
        #   if_signal
        #       │
        #       ▼
        #   create_range_doppler_map()
        #       │  1. Reshape IF into [N_reps × N_chirp_samples]
        #       │  2. Window each row (Hanning on fast-time)
        #       │  3. Range FFT along rows      → range axis
        #       │  4. Window each column (Hanning on slow-time)
        #       │  5. Doppler FFT along columns → velocity axis
        #       │  (for triangle: deinterleave up/down first, produce two maps)
        #       │
        #       ▼
        #   rd_map [N_reps × N_chirp_samples]
        #       │
        #       ├──► estimate_distance()   → argmax along range axis
        #       └──► estimate_velocity()   → argmax along Doppler axis
        
        # 1. Reshape IF into [N_reps x N_chirp_samples]
        N_chirp_samples = int(np.ceil(self.chirp_duration * self.fs))


        if self.triangle_en:
            if_signal = a_if_signal[:2 * self.chirp_reps * N_chirp_samples]
            full_matrix_iq = if_signal.reshape(2 * self.chirp_reps, N_chirp_samples) 
            up_matrix_iq = full_matrix_iq[0::2, :]
            down_matrix_iq = full_matrix_iq[1::2, :]
            T_rep = 2 * self.chirp_duration
        else:
            if_signal = a_if_signal[:self.chirp_reps * N_chirp_samples]
            up_matrix_iq = if_signal.reshape(self.chirp_reps, N_chirp_samples) 
            T_rep = self.chirp_duration
        
        range_freqs = np.fft.fftfreq(N_chirp_samples, d=1/self.fs)
        doppler_freqs = np.fft.fftshift(np.fft.fftfreq(self.chirp_reps, d=T_rep))
        ranges = range_freqs * c / (2 * self.chirp_bw / self.chirp_duration)
        velocities = doppler_freqs * c / (2 * self.chirp_fc)
        
        # Mask negative range bins (aliases)
        pos = ranges >= 0

        # 2. Apply windows
        rows, cols = up_matrix_iq.shape
        range_window = np.blackman(cols)
        doppler_window = np.hanning(rows)
        
        up_matrix_iq *= range_window[np.newaxis, :] * doppler_window[:, np.newaxis]
        if self.triangle_en:
            down_matrix_iq *= range_window[np.newaxis, :] * doppler_window[:, np.newaxis]

        # 3. Generate RD map
        up_matrix_rd = np.fft.fftshift(np.fft.fft2(up_matrix_iq), axes=0)
        magnitude_db_rd_up = 20 * np.log10(np.abs(up_matrix_rd[:, pos]) + 1e-12)
        if self.triangle_en:
            down_matrix_rd = np.fft.fftshift(np.fft.fft2(np.conj(down_matrix_iq)), axes=0)
            magnitude_db_rd_down = 20 * np.log10(np.abs(down_matrix_rd[:, pos]) + 1e-12)

        # 5. Plot
        if self.triangle_en:
            fig, (ax1, ax2) = plt.subplots(nrows=2, sharex=True)
            mesh1 = ax1.pcolormesh(ranges[pos], velocities, magnitude_db_rd_up, shading='auto', cmap='jet')
            cbar1 = fig.colorbar(mesh1, ax=ax1, label='dB')
            ax1.set_ylabel('Velocity (m/s)')
            ax1.set_title('Up-chirp')
            mesh2 = ax2.pcolormesh(ranges[pos], np.flip(velocities), magnitude_db_rd_down, shading='auto', cmap='jet')
            cbar2 = fig.colorbar(mesh2, ax=ax2, label='dB')
            ax2.set_xlabel('Range (m)')
            ax2.set_ylabel('Velocity (m/s)')
            ax2.set_title('Down-chirp')
            fig.suptitle("Range-Doppler Map", fontsize=16, fontweight="bold")
        else:
            plt.figure()
            plt.pcolormesh(ranges[pos], velocities, magnitude_db_rd_up, shading='auto', cmap='jet')
            plt.colorbar(label='dB')
            plt.xlabel('Range (m)')
            plt.ylabel('Velocity (m/s)')
            plt.title('Range-Doppler Map')
        if self.plot:
            plt.show()

        # 4. Fetch targets
        # maybe use CFAR here?
        
    def estimate_velocity(self):
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

    def add_velocity(self, a_signal, a_velocity):
        """
        Add Doppler shift
        """
        # Calculate Doppler shift
        doppler_freq = 2 * a_velocity * self.fmcw_model.chirp_fc / c
        # Create signal
        t = np.arange(0, len(a_signal) * (1/self.fmcw_model.fs), (1/self.fmcw_model.fs))
        doppler_signal = np.exp(2 * np.pi * 1j * doppler_freq * t)
        # Mix signals
        return a_signal * doppler_signal

    def add_delay(self, XXX):
        pass

    def simulate_received_signal(self, a_tx_signal, a_targets, a_noise_db=0):
        """
        Simulate attenuated return echo
        """
        # 1. Add Velocity
        rx_signal_doppler = self.add_velocity(a_tx_signal, a_target_velocity)
        # 2. Echo
        tau_s = 2 * a_target_distance / c
        tau_samples = int(np.ceil(tau_s * self.fmcw_model.fs))
        # Extend time vector
        rx_signal = np.zeros(len(rx_signal_doppler) + tau_samples, dtype=complex)
        # Insert attenuated original (w Doppler)
        rx_signal[tau_samples:] = rx_signal_doppler 
        # (Optional) Add noise
        if a_noise_db > 0:
            rx_signal += np.random.rand(len(rx_signal)) * (10.0 ** (a_noise_db / 20)) 
        # Shorten Rx signal to fit Tx
        rx_signal = rx_signal[:len(a_tx_signal)]

        return rx_signal

    def run_simulation(self, a_targets):
        # Generate chirp
        tx_signal = self.fmcw_model.generate_chirp_sequence()
        # Simulate echo
        # TODO need multiple rx signals (added together?)
        rx_signal = self.simulate_received_signal(a_tx_signal=tx_signal, a_targets=targets)
        if_signal = self.fmcw_model.mix_signals(a_tx_signal=tx_signal, a_rx_signal=rx_signal)
        self.fmcw_model.create_range_doppler_plot(a_if_signal=if_signal)
        estimated_distance = self.fmcw_model.estimate_distance(if_signal, self.fmcw_model.fs)
        estimated_velocity = self.fmcw_model.estimate_velocity(if_signal, self.fmcw_model.fs)
        return 0,0
    

if __name__ == "__main__":
    # ===============================================================================
    # PARAMETERS
    CHIRP_FC_HZ = 900e6
    CHIRP_BW_HZ = 56.6e6
    CHIRP_DURATION_S = 100e-6
    CHIRP_REPS = 128
    SAMPLING_RATE_HZ = 200e6
    TRIANGLE = False

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

    # Generate targets
    targets = [
        {
            "distance": 2489, # Target distance in meters
            "velocity": -118, # Target velocite in m/s
            "amplitude": 1.0,
        },
        {
            "distance": 105, # Target distance in meters
            "velocity": 0, # Target velocite in m/s
            "amplitude": 1.0,
        },
        {
            "distance": 800, # Target distance in meters
            "velocity": 10, # Target velocite in m/s
            "amplitude": 1.0,
        }
    ]
    # Run simulation
    estimated_distance = soft_fmcw_model.run_simulation(a_targets=targets)
    print(f"Estimated Distance: {np.mean(estimated_distance):.2f} m (True Distance: {target_distance} m)")