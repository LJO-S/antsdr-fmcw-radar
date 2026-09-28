"""
K2 recorder (Phase 6 guide Section 3). One directory per session, one config per
session. Worker-owned: opened and closed between CPIs, never from the GUI thread.

    <root>/<YYYYmmdd-HHMMSS>/
        session.json      asdict(cfg), fake targets incl. t_spawn, MAGIC,
                          git describe, wall-clock start, notes
        blocks.bin        raw int16 I/Q blocks as the DMA delivered them
                          (before trim and before TargetSim)
        index.csv         monotonic_t, byte_offset, n_bytes - flushed per line
        detections.jsonl  one line per CPI: {"block", "mti", "targets"}
"""

import dataclasses
import json
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np

from common import config, fabric_regs

SESSION_JSON = "session.json"
BLOCKS_BIN = "blocks.bin"
INDEX_CSV = "index.csv"
DETECTIONS_JSONL = "detections.jsonl"

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / "data" / "sessions"


# ===================================================================================
def git_describe() -> str:
    """`git describe --always --dirty` of this repo, or "unknown" if git fails."""
    # TODO K2
    raise NotImplementedError


# ===================================================================================
class Recorder:
    def __init__(self, a_root: Path = DEFAULT_ROOT):
        self.root = Path(a_root)
        self.session_dir: Path | None = None
        self._blocks = None  # blocks.bin, "ab"
        self._index = None  # index.csv, line-buffered
        self._dets = None  # detections.jsonl, line-buffered
        self._offset = 0  # bytes written to blocks.bin
        self._n_blocks = 0

    @property
    def active(self) -> bool:
        return self.session_dir is not None

    def open(
        self,
        a_config: config.RadarConfig,
        a_fake_targets: list[dict],
        a_notes: str = "",
    ) -> Path:
        """Create <root>/<timestamp>/, write session.json, open the three streams."""
        # TODO K2: session.json = {"config": dataclasses.asdict(a_config),
        #   "fake_targets": a_fake_targets (TargetSim.snapshot()),
        #   "magic": fabric_regs.MAGIC, "git": git_describe(),
        #   "start": datetime.now().isoformat(), "notes": a_notes}
        raise NotImplementedError

    def write_block(self, a_raw: np.ndarray, a_t: float) -> int:
        """Append one raw int16 block + its index line. Returns the block number."""
        # TODO K2
        raise NotImplementedError

    def write_detections(self, a_block: int, a_mti: bool, a_targets: list[dict]):
        """One jsonl line per CPI. MTI is per line: the checkbox changes mid-session."""
        # TODO K2
        raise NotImplementedError

    def close(self):
        """Flush and close everything. Safe to call when not active."""
        # TODO K2
        raise NotImplementedError


if __name__ == "__main__":
    print("Not standalone. Run 'python -m online.app'")
