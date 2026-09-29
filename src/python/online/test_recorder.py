"""
online/test_recorder.py

No-board round trip: Recorder writes a session into a temp dir, offline.replay
reads it back. Run as __main__.
"""

import builtins
import csv
import errno
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

import common.config as config
import common.dsp as dsp
from common import fabric_regs
from offline import replay
from online import capture, processing, recorder
from online.recorder import Recorder
from online.target_sim import TargetSim

SEED = 0
IMPORT_ROOT = Path(__file__).resolve().parents[1]  # src/python


class FullDisk:
    """A stream on a full disk: the unwritten bytes stay pending, so close() fails
    with the same error as the write did."""

    def write(self, a_data):
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self):
        raise OSError(errno.ENOSPC, "No space left on device")

    def close(self):
        self.flush()


def fabric_cfg(**a_overrides):
    # Fabric /8 without loopback noise: nothing random, so replay must be exact.
    # 32 reps keeps process_cpi fast.
    fields = dict(FABRIC_DECHIRP_EN=True, SDR_LOOPBACK_EN=False, CHIRP_REPS=32)
    fields.update(a_overrides)
    return config.RadarConfig(**fields)


def raw_from_iq(a_iq):
    """Inverse of dsp.iq_from_raw, up to int16 rounding."""
    scaled = np.round(a_iq * (2**11 - 1))
    raw = np.empty(2 * len(a_iq), dtype=np.int16)
    raw[0::2] = scaled.real
    raw[1::2] = scaled.imag
    return raw


def if_blocks(a_cfg, a_n, a_ranges=()):
    """a_n int16 blocks sized like the fabric DMA delivers them (CPI + margin):
    noise plus a stationary beat tone per range in a_ranges."""
    n = (a_cfg.CHIRP_REPS + a_cfg.SDR_RX_MARGIN_PERIODS) * a_cfg.N_IF
    t_fast = (np.arange(n) % a_cfg.N_IF) / a_cfg.FS_IF  # resets every chirp
    S = a_cfg.CHIRP_BW_HZ / a_cfg.T_EFF
    rng = np.random.default_rng(SEED)
    blocks = []
    for _ in range(a_n):
        iq = 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
        for r in a_ranges:
            iq += 0.3 * np.exp(2j * np.pi * S * 2 * r / config.c * t_fast)
        blocks.append(raw_from_iq(iq))
    return blocks


def record_session(a_root, a_cfg, a_blocks, a_sim, a_t0=1000.0, a_dt=0.04):
    """The worker's per-CPI loop on a synthetic clock: record, prepare, process,
    record detections. MTI flips mid-session like the live checkbox.
    Returns (session_dir, targets per block)."""
    ctx = dsp.build_cpi_context(a_config=a_cfg)
    rec = Recorder(a_root)
    session_dir = rec.open(a_cfg, a_sim.snapshot(), "test")
    per_block = []
    for k, raw in enumerate(a_blocks):
        t = a_t0 + k * a_dt
        a_cfg.MTI_EN = k % 3 == 2
        block_no = rec.write_block(raw, t)
        rx = capture.prepare_block(dsp.iq_from_raw(raw), a_cfg, ctx, a_sim, t)
        targets = processing.process_rx_data(a_rx=rx, a_config=a_cfg, a_ctx=ctx)[2]
        rec.write_detections(block_no, a_cfg.MTI_EN, targets)
        per_block.append(targets)
    rec.close()
    return session_dir, per_block


def test_blocks_roundtrip():
    # N random int16 blocks in -> byte-identical blocks out, index.csv offsets contiguous
    rng = np.random.default_rng(SEED)
    blocks = [
        rng.integers(-2048, 2048, size=2 * n, dtype=np.int16) for n in (707, 1, 5000, 707)
    ]
    times = [1000.0, 1000.1 + 1e-9, 1000.25, 1234.5678901234]
    with tempfile.TemporaryDirectory() as d:
        rec = Recorder(Path(d))
        session_dir = rec.open(fabric_cfg(), [])
        for k, (raw, t) in enumerate(zip(blocks, times)):
            assert rec.write_block(raw, t) == k
        rec.close()

        got = list(replay.iter_blocks(session_dir))
        assert len(got) == len(blocks), len(got)
        for k, (block_no, t, raw) in enumerate(got):
            assert block_no == k
            assert t == times[k], (t, times[k])  # repr() round-trips exactly
            assert np.array_equal(raw, blocks[k]), k

        with open(session_dir / recorder.INDEX_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
        offset = 0
        for row, raw in zip(rows, blocks):
            assert int(row["byte_offset"]) == offset
            assert int(row["n_bytes"]) == raw.nbytes
            offset += raw.nbytes
        assert (session_dir / recorder.BLOCKS_BIN).stat().st_size == offset

        # A second session in the same second gets its own directory, and an
        # empty session iterates to nothing
        empty_dir = rec.open(fabric_cfg(), [])
        rec.close()
        assert empty_dir != session_dir
        assert list(replay.iter_blocks(empty_dir)) == []

        # A normalized block must be refused, not written
        rec.open(fabric_cfg(), [])
        try:
            rec.write_block(dsp.iq_from_raw(blocks[0]), 0.0)
        except TypeError:
            pass
        else:
            raise AssertionError("write_block accepted a complex block")
        finally:
            rec.close()


def test_config_roundtrip():
    # A non-default RadarConfig (e.g. FABRIC_DECHIRP_EN, TRIANGLE_EN) comes back ==
    cfg = fabric_cfg(TRIANGLE_EN=True, MTI_EN=True, CHIRP_REPS=64, CHIRP_BW_HZ=25e6)
    assert cfg != config.RadarConfig()
    with tempfile.TemporaryDirectory() as d:
        rec = Recorder(Path(d))
        session_dir = rec.open(cfg, [], "corner reflector at 42.0 m")
        rec.close()
        got, session = replay.load_session(session_dir)

    assert got == cfg
    # Properties recompute from the fields
    assert got.N_IF == cfg.N_IF and got.MAX_RANGE == cfg.MAX_RANGE
    assert got.MAX_VELOCITY == cfg.MAX_VELOCITY
    assert session["magic"] == fabric_regs.MAGIC
    assert session["notes"] == "corner reflector at 42.0 m"
    assert session["git"] and session["start"]


def test_replay_matches_detections():
    # Fabric-mode int16 blocks with beat tones, detections recorded per block,
    # replay.check() passes; flip one stored target and it fails
    cfg = fabric_cfg()
    blocks = if_blocks(cfg, 6, a_ranges=(150.0, 420.0))
    with tempfile.TemporaryDirectory() as d:
        session_dir, per_block = record_session(Path(d), cfg, blocks, TargetSim())
        # MTI (blocks 2 and 5) removes the stationary tones; the others see them
        assert all(per_block[k] for k in (0, 1, 3, 4)), per_block
        assert replay.check(session_dir)

        dets_path = session_dir / recorder.DETECTIONS_JSONL
        lines = [json.loads(s) for s in dets_path.read_text().splitlines()]
        lines[1]["targets"][0]["r"] = np.nextafter(lines[1]["targets"][0]["r"], np.inf)
        dets_path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        assert not replay.check(session_dir), "a one-ulp change must fail the check"


def test_target_sim_deterministic():
    # Same snapshot + same a_now sequence -> identical apply_if output (noise off),
    # including a target that expires mid-sequence
    cfg = fabric_cfg()
    ctx = dsp.build_cpi_context(a_config=cfg)
    if_raw = dsp.iq_from_raw(if_blocks(cfg, 1)[0])[: cfg.CHIRP_REPS * cfg.N_IF]

    live = TargetSim()
    live.set_targets(
        [
            dict(r0=100.0, v0=10.0, a0=0.0, amp=1.0, duration=0.1),
            dict(r0=300.0, v0=-5.0, a0=2.0, amp=1.0, duration=10.0),
        ]
    )
    snap = live.snapshot()
    t0 = snap[0]["t_spawn"]
    times = [t0 + 0.02, t0 + 0.05, t0 + 0.12, t0 + 0.15]  # target 0 expires at 0.12

    a = TargetSim()
    a.restore(snap)
    b = TargetSim()
    b.restore(snap)
    assert a.snapshot() == snap, "restore() must keep the recorded t_spawn"

    outs = []
    for t in times:
        out_a = a.apply_if(if_raw, cfg, ctx, a_now=t)
        out_b = b.apply_if(if_raw, cfg, ctx, a_now=t)
        assert np.array_equal(out_a, out_b), t
        outs.append(out_a)
    assert not np.array_equal(outs[0], outs[1]), "a_now must move the targets"
    assert a.fake_targets[0].t_spawn == times[2], "expiry resets t_spawn to a_now"
    assert a.fake_targets[1].t_spawn == t0
    assert a.snapshot() == b.snapshot()


def test_replay_with_fake_targets():
    # As test_replay_matches_detections, with moving fake targets recorded via
    # snapshot(); replay.check() passes
    cfg = fabric_cfg()
    blocks = if_blocks(cfg, 8)
    sim = TargetSim()
    # 0.2 s lifetime over 8 x 40 ms: expires and respawns mid-session
    sim.set_targets([dict(r0=200.0, v0=-15.0, a0=0.0, amp=2.0, duration=0.2)])
    t0 = sim.fake_targets[0].t_spawn
    with tempfile.TemporaryDirectory() as d:
        session_dir, per_block = record_session(Path(d), cfg, blocks, sim, a_t0=t0)
        # Moving, so MTI keeps it: every block sees it between 197 and 200 m
        for k, targets in enumerate(per_block):
            assert any(abs(tg["r"] - 198.5) < 6.0 for tg in targets), (k, targets)
        assert replay.check(session_dir)


def test_player_seek_matches_sequential():
    # Player jumping around (back, forward, the same block twice) renders every
    # block exactly as walking in order does, across a fake-target respawn
    cfg = fabric_cfg()
    blocks = if_blocks(cfg, 8)
    sim = TargetSim()
    sim.set_targets([dict(r0=200.0, v0=-15.0, a0=0.0, amp=2.0, duration=0.2)])
    t0 = sim.fake_targets[0].t_spawn
    with tempfile.TemporaryDirectory() as d:
        session_dir, _ = record_session(Path(d), cfg, blocks, sim, a_t0=t0)
        walk = replay.Player(session_dir)
        in_order = [walk.render(k) for k in range(walk.n_blocks)]
        jumper = replay.Player(session_dir)
        for k in (7, 2, 3, 6, 6, 0, 5, 1):
            frame = jumper.render(k)
            assert frame.targets == in_order[k].targets, k
            assert np.array_equal(frame.rx, in_order[k].rx), k
            assert frame.match, k
        assert jumper.duration == in_order[-1].t - in_order[0].t


def test_player_mti_override():
    # Forcing MTI on a CPI recorded without it removes the stationary tones, and
    # the frame reports the MTI it ran with; back to None follows the recording
    cfg = fabric_cfg()
    blocks = if_blocks(cfg, 2, a_ranges=(150.0, 420.0))
    with tempfile.TemporaryDirectory() as d:
        session_dir, per_block = record_session(Path(d), cfg, blocks, TargetSim())
        player = replay.Player(session_dir)
        assert player.render(0).mti is False and per_block[0]
        player.mti_override = True
        forced = player.render(0)
        assert forced.mti is True and not forced.match
        assert len(forced.targets) < len(per_block[0]), forced.targets
        player.mti_override = None
        assert player.render(0).match


def test_disk_error_ends_session_not_radar():
    # A full disk mid-session: no exception reaches the caller (capture_rx_data
    # included), the session ends, the error waits in take_failure() exactly once,
    # and the blocks written before it still replay
    cfg = fabric_cfg()
    ctx = dsp.build_cpi_context(a_config=cfg)
    raw = if_blocks(cfg, 1)[0]

    class FakeSDR:
        def read_raw_block(self):
            return raw

    with tempfile.TemporaryDirectory() as d:
        rec = Recorder(Path(d))
        session_dir = rec.open(cfg, [])
        assert rec.write_block(raw, 1.0) == 0
        real, rec._blocks = rec._blocks, FullDisk()
        rx, block_no = capture.capture_rx_data(cfg, ctx, FakeSDR(), TargetSim(), rec)
        real.close()
        assert block_no is None and len(rx) == cfg.CHIRP_REPS * cfg.N_IF
        assert not rec.active
        err = rec.take_failure()
        assert isinstance(err, OSError) and err.errno == errno.ENOSPC, err
        assert rec.take_failure() is None, "a failure is reported once"
        rec.close()  # already closed: a no-op, no raise
        assert len(list(replay.iter_blocks(session_dir))) == 1

        # The same for the detections stream
        rec.open(cfg, [])
        rec.write_block(raw, 2.0)
        real, rec._dets = rec._dets, FullDisk()
        rec.write_detections(0, False, [])
        real.close()
        assert not rec.active
        assert isinstance(rec.take_failure(), OSError)


def test_open_failure_leaves_nothing():
    # A failing open() raises, leaves no directory, no open stream and no
    # failure for the worker (open errors are raised, not stored)
    calls = []

    def failing_open(*a_args, **a_kwargs):
        calls.append(a_args[0])
        if len(calls) == 3:  # detections.jsonl
            raise OSError(errno.EACCES, "Permission denied")
        return builtins.open(*a_args, **a_kwargs)

    with tempfile.TemporaryDirectory() as d:
        rec = Recorder(Path(d))
        recorder.open = failing_open  # shadows the builtin inside recorder.py
        try:
            rec.open(fabric_cfg(), [])
        except OSError:
            pass
        else:
            raise AssertionError("open() must raise")
        finally:
            del recorder.open
        assert list(Path(d).iterdir()) == [], list(Path(d).iterdir())
        assert not rec.active
        assert rec._blocks is None and rec._index is None and rec._dets is None
        assert rec.take_failure() is None


def test_replay_survives_crash():
    # Torn last lines are skipped, and an index that outlived its blocks (power
    # loss, no fsync) stops replay at the last complete block
    cfg = fabric_cfg()
    blocks = if_blocks(cfg, 4, a_ranges=(150.0,))
    with tempfile.TemporaryDirectory() as d:
        session_dir, _ = record_session(Path(d), cfg, blocks, TargetSim())
        with open(session_dir / recorder.DETECTIONS_JSONL, "a") as f:
            f.write('{"block": 4, "mti": fal')
        with open(session_dir / recorder.INDEX_CSV, "a") as f:
            f.write("1000.16,99")
        assert len(replay.load_detections(session_dir)) == 4
        assert len(list(replay.iter_blocks(session_dir))) == 4
        assert replay.check(session_dir)

        blocks_path = session_dir / recorder.BLOCKS_BIN
        with open(blocks_path, "r+b") as f:
            f.truncate(blocks_path.stat().st_size - 100)
        assert [b[0] for b in replay.iter_blocks(session_dir)] == [0, 1, 2]
        assert replay.check(session_dir)


def test_config_fields_filtered():
    # A session recorded before a field was removed (OLD_FIELD) or added (MTI_EN
    # missing) still loads: unknown keys dropped, missing ones defaulted
    with tempfile.TemporaryDirectory() as d:
        rec = Recorder(Path(d))
        session_dir = rec.open(fabric_cfg(MTI_EN=True), [])
        rec.close()
        path = session_dir / recorder.SESSION_JSON
        session = json.loads(path.read_text())
        session["config"]["OLD_FIELD"] = 1.0
        del session["config"]["MTI_EN"]
        path.write_text(json.dumps(session))
        got, _ = replay.load_session(session_dir)
    assert got == fabric_cfg(MTI_EN=config.RadarConfig().MTI_EN)


def test_replay_imports_without_iio():
    # Replay and the capture path must not need libiio (online.sdr imports iio)
    probe = (
        "import sys; sys.modules['iio'] = None; "
        "import offline.replay, online.capture, online.recorder"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe], cwd=IMPORT_ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


if __name__ == "__main__":
    tests = [
        test_blocks_roundtrip,
        test_config_roundtrip,
        test_replay_matches_detections,
        test_target_sim_deterministic,
        test_replay_with_fake_targets,
        test_player_seek_matches_sequential,
        test_player_mti_override,
        test_disk_error_ends_session_not_radar,
        test_open_failure_leaves_nothing,
        test_replay_survives_crash,
        test_config_fields_filtered,
        test_replay_imports_without_iio,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")

    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
