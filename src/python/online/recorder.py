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
import shutil
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
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip()


# ===================================================================================
class Recorder:
    """
    A disk error never escapes write_block, write_detections or close: it ends the
    session (files closed, active False) and waits in `failure` until the worker
    takes it, reports it and stops recording. open() raises instead, and leaves
    nothing behind.
    """

    def __init__(self, a_root: Path = DEFAULT_ROOT):
        self.root = Path(a_root)
        self.session_dir: Path | None = None
        self.failure: OSError | None = None
        self._blocks = None  # blocks.bin
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
        """Create <root>/<timestamp>/, open the three streams, write session.json."""
        # One session at a time: RE-CONFIGURE just calls open() again
        self.close()

        start = datetime.now()
        # Serialized before anything touches the disk
        session = json.dumps(
            {
                "config": dataclasses.asdict(a_config),
                "fake_targets": a_fake_targets,
                "magic": fabric_regs.MAGIC,
                "git": git_describe(),
                "start": start.isoformat(),
                "notes": a_notes,
            },
            indent=2,
        )
        session_dir = self._make_dir(start.strftime("%Y%m%d-%H%M%S"))
        try:
            self._blocks = open(session_dir / BLOCKS_BIN, "wb")
            self._index = open(session_dir / INDEX_CSV, "w", buffering=1, newline="")
            self._dets = open(session_dir / DETECTIONS_JSONL, "w", buffering=1)
            self._index.write("monotonic_t,byte_offset,n_bytes\n")
            # Last, so a directory with a session.json always has all three streams
            (session_dir / SESSION_JSON).write_text(session)
        except OSError:
            self._close_files()
            shutil.rmtree(session_dir, ignore_errors=True)
            raise
        self._offset = 0
        self._n_blocks = 0
        self.session_dir = session_dir
        return session_dir

    def _make_dir(self, a_stamp: str) -> Path:
        """<root>/<stamp>/, with a -1, -2, ... suffix if that second is taken."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / a_stamp
        n = 0
        while True:
            try:
                path.mkdir()
                return path
            except FileExistsError:
                n += 1
                path = self.root / f"{a_stamp}-{n}"

    def write_block(self, a_raw: np.ndarray, a_t: float) -> int | None:
        """Append one raw int16 block + its index line. Returns the block number, or
        None if a disk error just ended the session (see `failure`)."""
        if not self.active:
            raise RuntimeError("write_block() without an open session")
        # A normalized complex block would be written without complaint and
        # replay would read it back as garbage
        if a_raw.dtype != np.int16:
            raise TypeError(f"expected a raw int16 block, got {a_raw.dtype}")

        # Written straight from the array: a DMA block is already contiguous, so
        # this is no copy
        data = np.ascontiguousarray(a_raw)
        try:
            self._blocks.write(data)
            # Block before its index line: after a process crash index.csv never
            # points past the end of blocks.bin (a power loss can still reorder
            # them - replay.iter_blocks checks)
            self._blocks.flush()
            # repr(): shortest string that round-trips the float exactly
            self._index.write(f"{float(a_t)!r},{self._offset},{data.nbytes}\n")
        except OSError as e:
            self._fail(e)
            return None

        block_no = self._n_blocks
        self._offset += data.nbytes
        self._n_blocks += 1
        return block_no

    def write_detections(self, a_block: int, a_mti: bool, a_targets: list[dict]):
        """One jsonl line per CPI. MTI is per line: the checkbox changes mid-session."""
        if not self.active:
            raise RuntimeError("write_detections() without an open session")
        line = {"block": int(a_block), "mti": bool(a_mti), "targets": a_targets}
        try:
            # default=float: numpy scalars that are not float subclasses (float32)
            self._dets.write(json.dumps(line, default=float) + "\n")
        except OSError as e:
            self._fail(e)

    def close(self):
        """Flush and close everything. Safe to call when not active. Never raises: a
        failed flush lands in `failure`."""
        err = self._close_files()
        if err is not None and self.failure is None:
            self.failure = err

    def take_failure(self) -> OSError | None:
        """The disk error that ended a session since the last call, or None."""
        err, self.failure = self.failure, None
        return err

    def _fail(self, a_err: OSError):
        # Closing flushes the same pending bytes and fails again; the first error
        # is the one worth reporting
        self._close_files()
        if self.failure is None:
            self.failure = a_err

    def _close_files(self) -> OSError | None:
        """Close all three even if one fails. Returns the first failure."""
        files = (self._blocks, self._index, self._dets)
        self._blocks = self._index = self._dets = None
        self.session_dir = None
        err = None
        for f in files:
            if f is None:
                continue
            try:
                f.close()
            except OSError as e:
                err = err or e
        return err


if __name__ == "__main__":
    print("Not standalone. Run 'python -m online.app'")
