import logging

import numpy as np
from online import sdr, target_sim
import common.dsp as dsp
import common.config as config

logger = logging.getLogger(__name__)


def capture_rx_data(
    a_config: config.RadarConfig,
    a_ctx: dsp.CPIContext,
    a_sdr: sdr.AntSDR,
    a_target_sim: target_sim.TargetSim,
) -> np.ndarray:
    """
    Captures de-interleaved data and frame-syncs it.

    FABRIC_DECHIRP_EN: the PL dechirps before the DMA and (sync_src=1) starts
    every DMA transfer on a chirp boundary, so rx is already the frame-aligned
    IF stream - nothing to search, no TX reference here. TargetSim injects fake
    targets as beat tones directly onto the IF stream via apply_if() - this is
    software-only, downstream of the DMA, and never exercises the fabric.
    """
    # 1. Capture
    rx = a_sdr.read_block()
    if a_config.FABRIC_DECHIRP_EN:
        # sync_src=1 starts the DMA on the period-tagged sample; trim and cut come
        # out of the SDR_RX_MARGIN_PERIODS tail.
        rows = 2 * a_config.CHIRP_REPS if a_config.TRIANGLE_EN else a_config.CHIRP_REPS
        n_cpi = rows * a_config.N_IF
        trim = a_config.FABRIC_FRAME_TRIM
        rx = rx[trim : trim + n_cpi]
        return a_target_sim.apply_if(a_if_raw=rx, a_config=a_config, a_ctx=a_ctx)
    # 2. Simulate fake targets (if applicable)
    rx = a_target_sim.apply(a_rx_raw=rx, a_config=a_config)
    # 3. Frame-sync offset log (I3 determinism check). Only computed when DEBUG is
    #    enabled - otherwise this would double the frame-sync correlation cost.
    #    DMA TX: m_offset wanders block-to-block and across start() cycles.
    #    NCO TX (tx_src=1, sync_src=0): m_offset is a constant = TX->RX loopback
    #    latency; record it in TODO.md (I3). sync_src=1: m_offset ~= 0.
    if logger.isEnabledFor(logging.DEBUG):
        ref = a_ctx.tx_chirp
        m_offset = dsp.estimate_chirp_offset(a_rx=rx, a_ref_period=ref)
        logger.debug("chirp_offset=%d P=%d", m_offset, len(ref))
    # 4. Frame sync
    rx_aligned = dsp.frame_sync_linear(a_rx=rx, a_config=a_config, a_ctx=a_ctx)
    return rx_aligned


if __name__ == "__main__":
    print("Not a standalone script. Please run the main application instead.")
