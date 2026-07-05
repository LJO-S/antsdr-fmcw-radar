import numpy as np
from online import sdr
import common.dsp as dsp
import common.config as config


def capture_rx_data(
    a_config: config.RadarConfig, a_ctx: dsp.CPIContext, a_sdr: sdr.AntSDR
) -> np.ndarray:
    """
    Captures de-interleaved data and frame-syncs it
    """
    # 1. Capture
    rx = a_sdr.read_block()
    # 2. Frame sync
    if a_config.SDR_LOOPBACK_EN:
        # 2A. Simulate delay etc if enabled
        rx_aligned = dsp.frame_sync_linear(
            a_rx=rx,
            a_config=a_config,
            a_ctx=a_ctx,
            a_delay_samples=a_config.SDR_LOOPBACK_DELAY_SAMPLES,
        )
        rx_aligned = dsp.apply_doppler_shift(
            a_signal=rx_aligned,
            a_velocity=a_config.SDR_LOOPBACK_VELOCITY_MPS,
            a_config=a_config,
        )
        rx_aligned = dsp.apply_noise(
            a_signal=rx_aligned, a_snr_db=a_config.SDR_LOOPBACK_NOISE_SNR_DB
        )
    else:
        rx_aligned = dsp.frame_sync_linear(a_rx=rx, a_config=a_config, a_ctx=a_ctx)
    return rx_aligned


if __name__ == "__main__":
    print("Not a standalone script. Please run the main application instead.")
