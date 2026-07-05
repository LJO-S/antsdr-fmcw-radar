import numpy as np
from common import config, dsp


def process_rx_data(
    a_rx: np.ndarray, a_config: config.RadarConfig, a_ctx: dsp.CPIContext
) -> tuple[np.ndarray, np.ndarray, list[dict], np.ndarray, np.ndarray]:
    """
    Handle a single CPI of RX data, returning the RD maps and detected targets.
    """
    # Mix received signal with TX reference to get IF signal
    if_signal = dsp.mix_signal(a_rx_signal=a_rx, a_tx_signal=a_ctx.tx_seq)
    # Process IF signal to get range-Doppler map and detections
    rd_map_db_up, rd_map_db_down, detections, ranges, velocities = dsp.process_cpi(
        a_if_signal=if_signal, a_config=a_config, a_ctx=a_ctx
    )

    return rd_map_db_up, rd_map_db_down, detections, ranges, velocities


if __name__ == "__main__":
    print("Not a standalone script.")
