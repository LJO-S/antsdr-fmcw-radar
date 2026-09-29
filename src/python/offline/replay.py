"""
K2 replay (Phase 6 guide Section 3). Runs a recorded session through the same path
as the worker (fabric trim, or software frame sync + mix) into process_cpi.
Timestamps come from index.csv, so it is deterministic and faster than real time.

Exact only without random input: the loopback noise (SDR_LOOPBACK_EN with
SDR_LOOPBACK_NOISE_SNR_DB > 0) is redrawn on replay. Set the SNR to 0 to turn it
off for a session that --check should pass.

    python -m offline.replay <session_dir> [--check]

Player gives random access to any block (offline.playback, the GUI, is built on
it); replay() walks it in order.
"""

import argparse
import dataclasses
import json
from pathlib import Path

import numpy as np

# No online.sdr here: it imports iio, and replay must run without libiio
from common import config, dsp
from online import capture, processing, recorder, target_sim


# ===================================================================================
def load_session(a_dir: Path) -> tuple[config.RadarConfig, dict]:
    """RadarConfig(**fields) from session.json (properties recompute) + the metadata.

    Tolerates a RadarConfig that has changed since the recording: fields it no
    longer has are dropped, fields it gained take today's defaults. Both are
    printed, because either can make replay differ from what ran live."""
    session = json.loads((Path(a_dir) / recorder.SESSION_JSON).read_text())
    known = {f.name for f in dataclasses.fields(config.RadarConfig) if f.init}
    recorded = session["config"]
    dropped = sorted(set(recorded) - known)
    defaulted = sorted(known - set(recorded))
    if dropped:
        print(f"WARNING: session fields no longer in RadarConfig, ignored: {dropped}")
    if defaulted:
        print(f"WARNING: RadarConfig fields not in the session, defaulted: {defaulted}")
    fields = {k: v for k, v in recorded.items() if k in known}
    return config.RadarConfig(**fields), session


def _complete_lines(a_file):
    """The file's lines, minus a last one without a newline: a crash (or power loss)
    in the middle of writing it. Warns when that happens."""
    for text in a_file:
        if not text.endswith("\n"):
            print(f"WARNING: {Path(a_file.name).name} ends in a torn line, skipped")
            return
        yield text


def load_index(a_dir: Path) -> list[tuple[float, int, int]]:
    """index.csv as (monotonic_t, byte_offset, n_bytes) per block, cut at the first
    line whose block is not complete in blocks.bin."""
    a_dir = Path(a_dir)
    size = (a_dir / recorder.BLOCKS_BIN).stat().st_size
    index = []
    with open(a_dir / recorder.INDEX_CSV, newline="") as f:
        f.readline()  # header
        for block_no, text in enumerate(_complete_lines(f)):
            t, offset, n_bytes = text.rstrip("\n").split(",")
            offset, n_bytes = int(offset), int(n_bytes)
            # Nothing calls fsync: after a power loss index.csv can outlive the
            # blocks it points at, and a short block would crash prepare_block
            if offset + n_bytes > size:
                print(
                    f"WARNING: block {block_no} is past the end of "
                    f"{recorder.BLOCKS_BIN} (power loss?), stopping there"
                )
                break
            index.append((float(t), offset, n_bytes))
    return index


def open_blocks(a_dir: Path) -> np.ndarray | None:
    """blocks.bin as a read-only int16 memmap, or None if it is empty (np.memmap
    refuses an empty file: a session closed before its first block)."""
    path = Path(a_dir) / recorder.BLOCKS_BIN
    if path.stat().st_size == 0:
        return None
    return np.memmap(path, dtype=np.int16, mode="r")


def _block(a_blocks: np.ndarray, a_offset: int, a_n_bytes: int) -> np.ndarray:
    itemsize = a_blocks.itemsize
    return a_blocks[a_offset // itemsize : (a_offset + a_n_bytes) // itemsize]


def iter_blocks(a_dir: Path):
    """Yield (block_no, monotonic_t, raw int16 block) per index.csv line. Stops at
    the first line whose block is not complete in blocks.bin."""
    blocks = open_blocks(a_dir)
    if blocks is None:
        return
    for block_no, (t, offset, n_bytes) in enumerate(load_index(a_dir)):
        yield block_no, t, _block(blocks, offset, n_bytes)


def load_detections(a_dir: Path) -> dict[int, dict]:
    """detections.jsonl keyed by block number."""
    lines = {}
    with open(Path(a_dir) / recorder.DETECTIONS_JSONL) as f:
        for text in _complete_lines(f):
            if text.strip():
                line = json.loads(text)
                lines[line["block"]] = line
    return lines


def _as_json(a_targets: list[dict]) -> list[dict]:
    """The targets exactly as write_detections stores them."""
    return json.loads(json.dumps(a_targets, default=float))


# ===================================================================================
@dataclasses.dataclass
class Frame:
    """One replayed CPI."""

    block: int
    t: float  # monotonic capture time, as recorded
    rx: np.ndarray  # prepare_block output (the Signals tab needs it)
    outputs: tuple  # process_rx_data: up, down, targets, ranges, velocities, if
    mti: bool  # the MTI_EN this CPI ran with
    recorded: dict | None  # its detections.jsonl line, None if there is none

    @property
    def targets(self) -> list[dict]:
        return self.outputs[2]

    @property
    def match(self) -> bool | None:
        """Replayed detections identical to the recorded ones; None if unrecorded."""
        if self.recorded is None:
            return None
        return _as_json(self.targets) == self.recorded["targets"]


class Player:
    """
    Random access over a session: any block, with the fake targets exactly where
    they were live. replay() walks it in order; the playback GUI jumps around.

    The fake targets are the only state carried from block to block (an expiring
    target resets t_spawn). A step forward costs one CPI; any other jump restores
    the recorded snapshot and advances the kinematics up to the new block, which
    is cheap: no echoes, no processing.
    """

    def __init__(self, a_dir: Path):
        self.dir = Path(a_dir)
        self.cfg, self.session = load_session(self.dir)
        self.ctx = dsp.build_cpi_context(a_config=self.cfg)
        self.index = load_index(self.dir)
        self.lines = load_detections(self.dir)
        self._blocks = open_blocks(self.dir)
        self.sim = target_sim.TargetSim()
        self.sim.restore(self.session["fake_targets"])
        # MTI as recorded per CPI; a block without a detections line (crash between
        # the two writes) falls back to the session's value
        self._mti_default = self.cfg.MTI_EN
        self.mti_override: bool | None = None
        self.pos = -1  # last rendered block

    @property
    def n_blocks(self) -> int:
        return len(self.index)

    @property
    def duration(self) -> float:
        """Seconds from the first to the last block."""
        return self.index[-1][0] - self.index[0][0] if self.index else 0.0

    def render(self, a_block: int) -> Frame:
        if not 0 <= a_block < self.n_blocks:
            raise IndexError(f"block {a_block} outside [0, {self.n_blocks})")
        if a_block <= self.pos:
            self.sim.restore(self.session["fake_targets"])
            start = 0
        else:
            start = self.pos + 1
        if self.sim.fake_targets:
            for t_skipped, _, _ in self.index[start:a_block]:
                self.sim.advance(self.cfg, t_skipped)

        t, offset, n_bytes = self.index[a_block]
        line = self.lines.get(a_block)
        if self.mti_override is not None:
            self.cfg.MTI_EN = self.mti_override
        else:
            self.cfg.MTI_EN = line["mti"] if line is not None else self._mti_default
        rx = capture.prepare_block(
            a_rx=dsp.iq_from_raw(_block(self._blocks, offset, n_bytes)),
            a_config=self.cfg,
            a_ctx=self.ctx,
            a_target_sim=self.sim,
            a_now=t,
        )
        outputs = processing.process_rx_data(a_rx=rx, a_config=self.cfg, a_ctx=self.ctx)
        self.pos = a_block
        return Frame(a_block, t, rx, outputs, self.cfg.MTI_EN, line)


def replay(a_dir: Path):
    """Yield (block_no, t, targets) for every recorded block."""
    player = Player(a_dir)
    for block_no in range(player.n_blocks):
        frame = player.render(block_no)
        yield block_no, frame.t, frame.targets


def check(a_dir: Path) -> bool:
    """Replay and compare to detections.jsonl; print the first mismatch."""
    cfg, _ = load_session(a_dir)
    if cfg.SDR_LOOPBACK_EN and cfg.SDR_LOOPBACK_NOISE_SNR_DB > 0:
        print(
            "WARNING: loopback noise was on "
            f"({cfg.SDR_LOOPBACK_NOISE_SNR_DB} dB SNR) - replay redraws it, "
            "so an exact match is not expected"
        )
    lines = load_detections(a_dir)
    n_checked = 0
    n_missing = 0
    for block_no, t, targets in replay(a_dir):
        line = lines.get(block_no)
        if line is None:
            n_missing += 1
            continue
        # Exact equality: json round-trips float64 exactly, and both sides go
        # through the same serialization
        got = _as_json(targets)
        if got != line["targets"]:
            print(f"MISMATCH at block {block_no} (t = {t:.6f})")
            print(f"  recorded: {line['targets']}")
            print(f"  replayed: {got}")
            return False
        n_checked += 1
    if n_checked == 0:
        print("Nothing to check: no block has a detections line")
        return False
    print(f"OK: {n_checked} CPIs identical ({n_missing} blocks without detections)")
    return True


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
