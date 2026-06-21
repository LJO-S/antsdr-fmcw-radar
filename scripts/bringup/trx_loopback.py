import numpy as np
import matplotlib.pyplot as plt
import iio
import time

class AntSDR:
    def __init__(self, a_buffer_size):
        self.ip = "192.168.5.10"
        try:
            self.ctx = iio.Context(f"ip:{self.ip}")
            print(f"Connected to device at {self.ip}")
        except Exception as e:
            print(f"No devices found at specified IP address: {e}")
            raise

        # Get devices
        self.ctrl = self.ctx.find_device("ad9361-phy")
        self.tx = self.ctx.find_device("cf-ad9361-dds-core-lpc")
        self.rx = self.ctx.find_device("cf-ad9361-lpc")

        self.full_scale = 2**15 - 1
        self.buffer_size = a_buffer_size

    def cfg_tx_params(self, a_fs, a_freq_lo, a_bw, a_hw_gain):
        self.ctrl.find_channel("TX_LO", is_output=True).attrs["frequency"].value = str(int(a_freq_lo))
        self.ctrl.find_channel('voltage0', is_output=True).attrs["rf_bandwidth"].value = str(int(a_bw))
        self.ctrl.find_channel('voltage0', is_output=True).attrs["sampling_frequency"].value = str(int(a_fs))
        self.ctrl.find_channel('voltage0', is_output=True).attrs["hardwaregain"].value = str(a_hw_gain)

    def cfg_rx_params(self, a_fs, a_freq_lo, a_bw, a_gain_ctrl, a_hw_gain=30):
        self.ctrl.find_channel('RX_LO', is_output=True).attrs["frequency"].value = str(int(a_freq_lo))
        self.ctrl.find_channel('voltage0').attrs["rf_bandwidth"].value = str(int(a_bw))
        self.ctrl.find_channel('voltage0').attrs["sampling_frequency"].value = str(int(a_fs))
        self.ctrl.find_channel('voltage0').attrs["gain_control_mode"].value = a_gain_ctrl
        if a_gain_ctrl == "manual":
            self.ctrl.find_channel('voltage0').attrs["hardwaregain"].value = str(a_hw_gain)

    def cfg_rx_ch(self, a_en: bool):
        # Enable RX channels and create buffer
        self.rx.find_channel("voltage0").enabled = a_en
        self.rx.find_channel("voltage1").enabled = a_en
        if a_en:
            # Create buffer for RX
            self.rx_buff = iio.Buffer(self.rx, samples_count=self.buffer_size, cyclic=False)

    def cfg_tx_ch(self, a_en: bool):
        # Enable TX channels and create buffer
        self.tx.find_channel("voltage0", is_output=True).enabled = a_en
        self.tx.find_channel("voltage1", is_output=True).enabled = a_en
        if a_en:
            # Create buffer for TX
            self.tx_buff = iio.Buffer(self.tx, samples_count=self.buffer_size, cyclic=True)


    def set_tx_dma(self):
        self.tx.find_channel("TX1_I_F1", is_output=True).attrs["raw"].value = str(0)
        self.tx.find_channel("TX1_Q_F1", is_output=True).attrs["raw"].value = str(0)
        self.tx.find_channel("TX1_I_F2", is_output=True).attrs["raw"].value = str(0)
        self.tx.find_channel("TX1_Q_F2", is_output=True).attrs["raw"].value = str(0)

    def generate_tx_samples_sine(self, a_freq, a_fs, a_amp = 1.0):
        t = np.arange(self.buffer_size) / a_fs
        iq = a_amp * np.exp(2j * np.pi * a_freq * t)
        # Scale to 16-bit signed integer range
        scaled = iq * (self.full_scale)
        # Interleave I and Q samples
        interleaved = np.empty(2*len(scaled), dtype=np.int16)
        interleaved[0::2] = np.real(scaled).astype(np.int16)
        interleaved[1::2] = np.imag(scaled).astype(np.int16)
        print(len(interleaved), len(scaled))
        written = self.tx_buff.write(bytearray(interleaved))
        print(f"TX write: {written} bytes")
        self.tx_buff.push()
    
    def generate_tx_samples_chirp(self, a_f_start, a_f_end, a_fs, a_amp = 1.0):
        t = np.arange(self.buffer_size) / a_fs
        k = (a_f_end - a_f_start) / (self.buffer_size / a_fs)
        iq = a_amp * np.exp(2j * np.pi * (a_f_start * t + 0.5 * k * t**2))
        # Scale to 16-bit signed integer range
        scaled = iq * (self.full_scale)
        # Interleave I and Q samples
        interleaved = np.empty(2*len(scaled), dtype=np.int16)
        interleaved[0::2] = np.real(scaled).astype(np.int16)
        interleaved[1::2] = np.imag(scaled).astype(np.int16)
        self.tx_buff.write(bytearray(interleaved))
        self.tx_buff.push()

    def collect_rx_samples(self):
        self.rx_buff.refill()
        data = self.rx_buff.read()
        samples_iq = np.frombuffer(data, dtype=np.int16).astype(np.float32) / (2**11 - 1) # Normalize to [-1, 1]
        return samples_iq[0::2] + 1j * samples_iq[1::2]
    
    def set_loopback(self, a_en: bool):
        self.ctrl.debug_attrs["loopback"].value = str(int(a_en))
        print("Loopback=",self.ctrl.debug_attrs["loopback"].value)

    def close(self):
        self.rx_buff.cancel()
        self.tx_buff.cancel()
    
if __name__ == "__main__":
    # PARAMETERS
    FS = 4e6
    F_LO = 400e6
    BW = 4e6
    TX_HW_GAIN = -30
    RX_GAIN_CTRL = "slow_attack"
    RX_HW_GAIN = 30
    a_freq_sine = 200e3
    a_freq_chirp_start = 100e3
    a_freq_chirp_end = 200e3

    BUFFER_SIZE = 2**10

    # CREATE SDR OBJECT
    sdr = AntSDR(a_buffer_size=BUFFER_SIZE)

    sdr.cfg_tx_params(a_fs=FS, a_freq_lo=F_LO, a_bw=BW, a_hw_gain=TX_HW_GAIN)
    sdr.cfg_rx_params(a_fs=FS, a_freq_lo=F_LO, a_bw=BW, a_gain_ctrl=RX_GAIN_CTRL, a_hw_gain=RX_HW_GAIN)
    sdr.set_loopback(a_en=True)        # set AFTER calibration so it isn't reset
    sdr.set_tx_dma()                   # disable DDS before any buffer is created
    sdr.cfg_rx_ch(a_en=True)  # RX buffer first (matches reference)
    sdr.cfg_tx_ch(a_en=True)  # TX buffer second

    print("Loopback=",sdr.ctrl.debug_attrs["loopback"].value)


    # GENERATE AND TRANSMIT SINE WAVE
    sdr.generate_tx_samples_sine(a_freq=a_freq_sine, a_fs=FS, a_amp=1.0)

    # Flush the RX DMA ring. It was armed when the buffer was created (before TX
    # was pushed), so the first few queued frames hold pre-TX idle = zeros. Only
    # refilling drains them -- sleeping does NOT, the stale frames stay queued.
    NUM_FLUSH = 8
    while np.max(np.abs( sdr.collect_rx_samples() )) < 0.01: # Wait until we see non-zero samples
        sdr.collect_rx_samples()

    # COLLECT MULTIPLE RUNS OF RX SAMPLES
    num_runs = 10
    rx_samples = np.empty((num_runs, BUFFER_SIZE), dtype=np.complex64)
    for i in range(num_runs):
        rx_samples[i] = sdr.collect_rx_samples()

    rx_samples_sine = rx_samples.flatten()

    # PLOT
    plt.figure()
    # plt.plot((np.arange(len(rx_samples_sine)) / FS) * 1e3, np.real(rx_samples_sine), label="I")
    # plt.plot((np.arange(len(rx_samples_sine)) / FS) * 1e3, np.imag(rx_samples_sine), label="Q")
    plt.plot(np.real(rx_samples_sine), label="I")
    plt.plot(np.imag(rx_samples_sine), label="Q")
    plt.xlabel("Time (ms)")
    plt.ylabel("Amplitude")
    plt.title("RX Samples - Sine Wave")
    plt.legend()
    plt.show()

    sdr.set_loopback(a_en=False) # Disable loopback mode
    sdr.close()
