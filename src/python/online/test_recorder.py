"""
online/test_recorder.py

No-board round trip: Recorder writes a session into a temp dir, offline.replay
reads it back. Run as __main__.
"""

import tempfile
from pathlib import Path

import numpy as np

import common.config as config
from offline import replay
from online.recorder import Recorder


def test_blocks_roundtrip():
    # N random int16 blocks in -> byte-identical blocks out, index.csv offsets contiguous
    # TODO K2
    raise NotImplementedError


def test_config_roundtrip():
    # A non-default RadarConfig (e.g. FABRIC_DECHIRP_EN, TRIANGLE_EN) comes back ==
    # TODO K2
    raise NotImplementedError


def test_replay_matches_detections():
    # Fabric-mode int16 blocks with beat tones, detections recorded per block,
    # replay.check() passes; flip one stored target and it fails
    # TODO K2
    raise NotImplementedError


def test_target_sim_deterministic():
    # Same snapshot + same a_now sequence -> identical apply_if output (noise off),
    # including a target that expires mid-sequence
    # TODO K2
    raise NotImplementedError


def test_replay_with_fake_targets():
    # As test_replay_matches_detections, with moving fake targets recorded via
    # snapshot(); replay.check() passes
    # TODO K2
    raise NotImplementedError


if __name__ == "__main__":
    tests = [
        test_blocks_roundtrip,
        test_config_roundtrip,
        test_replay_matches_detections,
        test_target_sim_deterministic,
        test_replay_with_fake_targets,
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
