import logging
import time
from typing import TYPE_CHECKING

import numpy as np
from online import recorder, target_sim
import common.dsp as dsp
import common.config as config

if TYPE_CHECKING:
    # Type hints only: sdr imports iio, and offline.replay imports this module
    from online import sdr

logger = logging.getLogger(__name__)


def capture_rx_data(
    a_config: config.RadarConfig,
    a_ctx: dsp.CPIContext,
    a_sdr: "sdr.AntSDR",
    a_target_sim: target_sim.TargetSim,
    a_recorder: recorder.Recorder = None,
) -> tuple[np.ndarray, int | None]:
    """
    Captures one block, records it if a session is open, and returns
    (prepare_block() output, block number or None when not recording).
    """
    # 1. Capture. One timestamp: it goes to the recorder and to TargetSim, so replay
    #    moves the fake targets exactly like the live run did.
    raw = a_sdr.read_raw_block()
    t = time.monotonic()
    block_no = None
    if a_recorder is not None and a_recorder.active:
        # None on a disk error: the recorder ends the session itself and never
        # raises, and the worker reports it before the next CPI
        block_no = a_recorder.write_block(raw, t)
    rx = prepare_block(
        a_rx=dsp.iq_from_raw(raw),
        a_config=a_config,
        a_ctx=a_ctx,
        a_target_sim=a_target_sim,
        a_now=t,
    )
    return rx, block_no


def prepare_block(
    a_rx: np.ndarray,
    a_config: config.RadarConfig,
    a_ctx: dsp.CPIContext,
    a_target_sim: target_sim.TargetSim,
    a_now: float,
) -> np.ndarray:
    """
    One IQ block -> one frame-aligned CPI with fake targets. Everything the worker
    does between the DMA and process_rx_data; offline.replay calls it too.

    FABRIC_DECHIRP_EN: the PL dechirps before the DMA and (sync_src=1) starts
    every DMA transfer on a chirp boundary, so rx is already the frame-aligned
    IF stream. TargetSim injects fake targets as beat tones directly onto the
    IF stream via apply_if() - this is software-only, downstream of the DMA,
    and never exercises the fabric.
    """
    rx = a_rx
    if a_config.FABRIC_DECHIRP_EN:
        # sync_src=1 starts the DMA on the period-tagged sample; trim and cut come
        # out of the SDR_RX_MARGIN_PERIODS tail.
        rows = 2 * a_config.CHIRP_REPS if a_config.TRIANGLE_EN else a_config.CHIRP_REPS
        n_cpi = rows * a_config.N_IF
        trim = a_config.FABRIC_FRAME_TRIM
        rx = rx[trim : trim + n_cpi]
        return a_target_sim.apply_if(
            a_if_raw=rx, a_config=a_config, a_ctx=a_ctx, a_now=a_now
        )
    # 2. Simulate fake targets (if applicable)
    rx = a_target_sim.apply(a_rx_raw=rx, a_config=a_config, a_now=a_now)
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
