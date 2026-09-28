"""
K2 replay (Phase 6 guide Section 3). Runs a recorded session through the same path
as the worker (fabric trim, or software frame sync + mix) into process_cpi.
Timestamps come from index.csv, so it is deterministic and faster than real time.

    python -m offline.replay <session_dir> [--check]
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from common import config, dsp
from online import recorder


# ===================================================================================
def load_session(a_dir: Path) -> tuple[config.RadarConfig, dict]:
    """RadarConfig(**fields) from session.json (properties recompute) + the metadata."""
    # TODO K2
    raise NotImplementedError


def iter_blocks(a_dir: Path):
    """Yield (block_no, monotonic_t, raw int16 block) per index.csv line."""
    # TODO K2: np.memmap blocks.bin once, slice by byte_offset / n_bytes
    raise NotImplementedError


def load_detections(a_dir: Path) -> dict[int, dict]:
    """detections.jsonl keyed by block number."""
    # TODO K2
    raise NotImplementedError


# ===================================================================================
def replay(a_dir: Path):
    """Yield (block_no, t, targets) for every recorded block."""
    # TODO K2: cfg, ctx = load_session + build_cpi_context; TargetSim.restore(
    #   session["fake_targets"]); per block: raw -> IQ (same conversion as
    #   sdr.read_block) -> the worker's capture path with a_now = t ->
    #   process_rx_data, with MTI_EN from that block's detections line
    raise NotImplementedError


def check(a_dir: Path) -> bool:
    """Replay and compare to detections.jsonl; print the first mismatch."""
    # TODO K2: exact equality - json round-trips float64 exactly
    raise NotImplementedError


# ===================================================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Replay a recorded K2 session")
    ap.add_argument("session", type=Path)
    ap.add_argument("--check", action="store_true", help="compare to detections.jsonl")
    args = ap.parse_args()

    if args.check:
        raise SystemExit(0 if check(args.session) else 1)
    for block_no, t, targets in replay(args.session):
        print(f"{block_no:6d}  t={t:10.3f}  {len(targets)} targets")
