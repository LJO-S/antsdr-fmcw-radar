import numpy as np
from online import sdr, target_sim
import common.dsp as dsp
import common.config as config


def capture_rx_data(
    a_config: config.RadarConfig,
    a_ctx: dsp.CPIContext,
    a_sdr: sdr.AntSDR,
    a_target_sim: target_sim.TargetSim,
) -> np.ndarray:
    """
    Captures de-interleaved data and frame-syncs it
    """
    # 1. Capture
    rx = a_sdr.read_block()
    # 2. Simulate fake targets (if applicable)
    rx = a_target_sim.apply(a_rx_raw=rx, a_config=a_config)
    # 3. Frame sync
    rx_aligned = dsp.frame_sync_linear(a_rx=rx, a_config=a_config, a_ctx=a_ctx)
    return rx_aligned


if __name__ == "__main__":
    print("Not a standalone script. Please run the main application instead.")
