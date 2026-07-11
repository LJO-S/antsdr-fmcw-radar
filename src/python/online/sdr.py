import iio
import common.config as config
import common.dsp as dsp
import numpy as np


class AntSDR:
    def __init__(self, a_radar_config: config.RadarConfig):
        self.config = a_radar_config
        self.full_scale = 2**15 - 1
        self.active = False

        # --------------------
        # Connect
        # --------------------
        try:
            self.ctx = iio.Context(f"ip:{self.config.SDR_IP}")
            print(f"Connected to {self.config.SDR_IP}")
        except Exception as e:
            print(f"Failed to connect to {self.config.SDR_IP}: {e}")
            raise

        # --------------------
        # Create context
        # --------------------
        self.ctrl = self.ctx.find_device("ad9361-phy")
        self.tx = self.ctx.find_device("cf-ad9361-dds-core-lpc")
        self.rx = self.ctx.find_device("cf-ad9361-lpc")

    def start(self):
        """
        Start TX and RX DMA for one CPI of data.
        """

        # --------------------
        # Configure TX
        # --------------------
        self.ctrl.find_channel("TX_LO", is_output=True).attrs["frequency"].value = str(
            int(self.config.CHIRP_FC_HZ)
        )
        self.ctrl.find_channel("voltage0", is_output=True).attrs[
            "rf_bandwidth"
        ].value = str(int(self.config.CHIRP_BW_HZ))
        self.ctrl.find_channel("voltage0", is_output=True).attrs[
            "sampling_frequency"
        ].value = str(int(self.config.FS))
        self.ctrl.find_channel("voltage0", is_output=True).attrs[
            "hardwaregain"
        ].value = str(self.config.SDR_TX_GAIN_DB)

        # --------------------
        # Configure RX
        # --------------------
        self.ctrl.find_channel("RX_LO", is_output=True).attrs["frequency"].value = str(
            int(self.config.CHIRP_FC_HZ)
        )
        self.ctrl.find_channel("voltage0").attrs["rf_bandwidth"].value = str(
            int(self.config.CHIRP_BW_HZ)
        )
        self.ctrl.find_channel("voltage0").attrs["sampling_frequency"].value = str(
            int(self.config.FS)
        )
        self.ctrl.find_channel("voltage0").attrs[
            "gain_control_mode"
        ].value = self.config.SDR_RX_GAIN_MODE
        self.ctrl.find_channel("voltage0").attrs["hardwaregain"].value = str(
            self.config.SDR_RX_GAIN_DB
        )

        # --------------------
        # Disable DDS
        # --------------------
        self.tx.find_channel("TX1_I_F1", is_output=True).attrs["raw"].value = str(0)
        self.tx.find_channel("TX1_Q_F1", is_output=True).attrs["raw"].value = str(0)
        self.tx.find_channel("TX1_I_F2", is_output=True).attrs["raw"].value = str(0)
        self.tx.find_channel("TX1_Q_F2", is_output=True).attrs["raw"].value = str(0)

        # --------------------
        # Enable TX and RX channels
        # --------------------
        chirp = dsp.generate_chirp(a_config=self.config)

        # Prepare TX int16 payload: scale then interleave I/Q
        scaled = chirp * self.full_scale
        interleaved = np.empty(2 * len(chirp), dtype=np.int16)
        interleaved[0::2] = np.real(scaled).astype(np.int16)
        interleaved[1::2] = np.imag(scaled).astype(np.int16)

        # Arm RX first so DMA is capturing before TX starts transmitting
        self.rx.find_channel("voltage0").enabled = True
        self.rx.find_channel("voltage1").enabled = True

        # --------------------
        # RX buffer (= one CPI + margin)
        # --------------------
        rx_buf_samples = (
            self.config.CHIRP_REPS + self.config.SDR_RX_MARGIN_PERIODS
        ) * len(chirp)
        self.rx_buff = iio.Buffer(self.rx, samples_count=rx_buf_samples, cyclic=False)

        # --------------------
        # Start TX cyclic DMA
        # --------------------
        self.tx.find_channel("voltage0", is_output=True).enabled = True
        self.tx.find_channel("voltage1", is_output=True).enabled = True
        self.tx_buff = iio.Buffer(self.tx, samples_count=len(chirp), cyclic=True)
        written = self.tx_buff.write(bytearray(interleaved))
        print(f"TX write: {written} bytes")
        self.tx_buff.push()

        # --------------------
        # Flush stale pre-TX RX frames
        # --------------------
        print("Flushing stale RX frames...")
        for _ in range(32):
            if np.max(np.abs(self._read_deinterleaved())) >= 0.01:
                break
        else:
            self.close()
            raise RuntimeError(
                "RX signal never became live after TX start — check loopback/cable"
            )
        print("RX live!")

        self.active = True

    def _read_raw(self):
        """
        Refill RX buffer and return raw bytes.
        """
        self.rx_buff.refill()
        return self.rx_buff.read()

    def _read_deinterleaved(self):
        """
        Refill RX buffer and return normalized+de-interlweaved complex64 array.
        """
        data = self._read_raw()
        raw = np.frombuffer(data, dtype=np.int16).astype(np.float32) / (2**11 - 1)
        return raw[0::2] + 1j * raw[1::2]

    def read_block(self):
        """
        Same as _read_deinterleaved() but public.
        """
        return self._read_deinterleaved()

    def set_loopback(self, a_en: bool):
        self.ctrl.debug_attrs["loopback"].value = str(int(a_en))
        if int(self.ctrl.debug_attrs["loopback"].value) == 0:
            print("Loopback OFF!")
        else:
            print("Loopback ON!")

    def close(self):
        if getattr(self, "rx_buff", None):
            self.rx_buff.cancel()
            del self.rx_buff
        if getattr(self, "tx_buff", None):
            self.tx_buff.cancel()
            del self.tx_buff
        self.tx.find_channel("voltage0", is_output=True).enabled = False
        self.tx.find_channel("voltage1", is_output=True).enabled = False
        self.rx.find_channel("voltage0").enabled = False
        self.rx.find_channel("voltage1").enabled = False
        self.active = False


if __name__ == "__main__":

    def add_test_noise(a_signal: np.ndarray, a_snr_db: float) -> np.ndarray:
        sig_rms = np.sqrt(np.mean(np.abs(a_signal) ** 2))
        noise_std = sig_rms / (10 ** (a_snr_db / 20)) / np.sqrt(2)
        noise = noise_std * (
            np.random.randn(*a_signal.shape) + 1j * np.random.randn(*a_signal.shape)
        )
        return a_signal + noise

    # Run a loopback test if this script is executed directly
    radar_config = config.RadarConfig()
    D = 400  # samples
    # Build context
    ctx = dsp.build_cpi_context(a_config=radar_config)
    P = len(ctx.tx_chirp)
    n_cpi = radar_config.CHIRP_REPS * P

    # Set loopback
    sdr = AntSDR(a_radar_config=radar_config)
    sdr.set_loopback(True)

    try:
        # Start TX and RX
        sdr.start()

        r_expected = config.c * D / (2 * radar_config.FS)  # ~2.65m per sample
        print(f"Expecting one target at {r_expected:.1f} m, ~0 m/s, kind='both'")
        for n in range(10):
            print(f"Reading block {n+1}/10...")
            # Read one CPI of data
            samples = sdr.read_block()

            # Frame sync: estimate chirp offset
            # Actual:
            # rx_aligned = dsp.frame_sync(a_rx=samples, a_config=radar_config, a_ctx=ctx)
            # For test:
            m = dsp.estimate_chirp_offset(a_rx=samples, a_ref_period=ctx.tx_chirp)
            start = (m - D) % P
            rx_delayed = samples[start : start + n_cpi]

            # Add some noise
            rx_delayed = add_test_noise(rx_delayed, a_snr_db=9.0)

            # Mix received signal with TX reference to get IF signal
            if_signal = dsp.mix_signal(a_rx_signal=rx_delayed, a_tx_signal=ctx.tx_seq)

            # Process IF signal to get range-Doppler map and detections
            rd_map_db_up, rd_map_db_down, detections, ranges, velocities = (
                dsp.process_cpi(a_if_signal=if_signal, a_config=radar_config, a_ctx=ctx)
            )

            print(f"\toffset m={m}, start={start}")
            for d in detections:
                print(f"  -> r={d['r']:.1f} m  v={d['v']:.2f} m/s  kind={d['kind']}")

            if n == 10 - 1:
                # Plot this stuff
                import matplotlib.pyplot as plt

                win = 2 * len(ctx.tx_chirp)
                fig, axs = plt.subplots(2, 3)

                axs[0, 0].plot(
                    dsp.inst_freq(ctx.tx_seq[:win], a_radar_config=radar_config), lw=0.6
                )
                axs[0, 0].set_title("TX reference")
                axs[0, 0].set_xlabel("sample")

                axs[0, 1].plot(
                    dsp.inst_freq(rx_delayed, a_radar_config=radar_config)[:win], lw=0.6
                )
                axs[0, 1].set_title(f"Delayed RX")
                axs[0, 1].set_xlabel("sample")

                axs[0, 2].plot(
                    dsp.inst_freq(if_signal, a_radar_config=radar_config)[:win], lw=0.6
                )
                axs[0, 2].set_title(f"IF Signal")
                axs[0, 2].set_xlabel("sample")

                axs[1, 0].imshow(
                    rd_map_db_up.T,
                    aspect="auto",
                    origin="lower",
                    extent=[velocities[0], velocities[-1], ranges[0], ranges[-1]],
                )
                axs[1, 0].set_title("Range-Doppler Map (Up)")
                axs[1, 0].set_xlabel("Velocity [m/s]")
                axs[1, 0].set_ylabel("Range [m]")

                if rd_map_db_down is not None:
                    axs[1, 1].imshow(
                        rd_map_db_down.T,
                        aspect="auto",
                        origin="lower",
                        extent=[velocities[0], velocities[-1], ranges[0], ranges[-1]],
                    )
                    axs[1, 1].set_title("Range-Doppler Map (Down)")
                    axs[1, 1].set_xlabel("Velocity [m/s]")
                    axs[1, 1].set_ylabel("Range [m]")

                # Do a scatter plot of detections
                axs[1, 2].scatter(
                    [d["v"] for d in detections],
                    [d["r"] for d in detections],
                    c="r",
                    marker="x",
                )
                axs[1, 2].set_xlim(velocities[0], velocities[-1])
                axs[1, 2].set_ylim(ranges[0], ranges[-1])
                axs[1, 2].set_title("Detections")
                axs[1, 2].set_xlabel("Velocity [m/s]")
                axs[1, 2].set_ylabel("Range [m]")
                axs[1, 2].grid(True)

                plt.tight_layout()
                plt.show()
    finally:
        sdr.set_loopback(False)
        sdr.close()
