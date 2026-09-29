import numpy as np
from common import config, dsp


def process_rx_data(
    a_rx: np.ndarray, a_config: config.RadarConfig, a_ctx: dsp.CPIContext
) -> tuple[np.ndarray, np.ndarray, list[dict], np.ndarray, np.ndarray, np.ndarray]:
    """
    Handle a single CPI of RX data, returning the RD maps and detected targets.
    """
    # Mix received signal with TX reference to get IF signal. In fabric IF mode
    # the PL already did this (IF = delayed_TX * conj(RX), same convention as
    # mix_signal), so a_rx is the IF stream - pass it straight through.
    if a_config.FABRIC_DECHIRP_EN:
        if_signal = a_rx
    else:
        if_signal = dsp.mix_signal(a_rx_signal=a_rx, a_tx_signal=a_ctx.tx_seq)
    # Process IF signal to get range-Doppler map and detections
    rd_map_db_up, rd_map_db_down, detections, ranges, velocities = dsp.process_cpi(
        a_if_signal=if_signal, a_config=a_config, a_ctx=a_ctx
    )

    return rd_map_db_up, rd_map_db_down, detections, ranges, velocities, if_signal


def spectrograms(
    a_rx: np.ndarray,
    a_if_signal: np.ndarray,
    a_config: config.RadarConfig,
    a_ctx: dsp.CPIContext,
):
    """
    (rx_spec, if_spec, t, f) over 2 chirp periods for the Signals tab. Shared by the
    live worker and playback.
    """
    if a_config.FABRIC_DECHIRP_EN:
        # rx is already the (possibly decimated) IF stream, at ctx.fs_if rather
        # than ctx tx-chirp/FS rate - slice and label the spectrogram accordingly.
        legs = 2 if a_config.TRIANGLE_EN else 1
        n2 = 2 * legs * a_ctx.N_if_samples  # 2 periods, IF domain
        rx_spec, t, f = dsp.spectrogram(
            a_signal=a_rx[:n2], a_config=a_config, a_fs=a_ctx.fs_if
        )
        # Rx is already the IF signal, so skip the separate IF plot
        return rx_spec, None, t, f
    n2 = 2 * len(a_ctx.tx_chirp)  # 2 chirps
    rx_spec, t, f = dsp.spectrogram(a_signal=a_rx[:n2], a_config=a_config)
    if_spec, _, _ = dsp.spectrogram(a_signal=a_if_signal[:n2], a_config=a_config)
    return rx_spec, if_spec, t, f


if __name__ == "__main__":
    print("Not a standalone script.")
