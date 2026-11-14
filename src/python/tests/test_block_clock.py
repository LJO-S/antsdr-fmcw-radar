"""
tests/test_block_clock.py

BlockClock: RX block capture midpoint from refill() entry times. Model (FIFO of n kernel
blocks, DMA back to back) measured on board with RX gain step at n = 1, 2, 4; tests pin
model, not board.
"""

import numpy as np

from online.block_clock import BlockClock

T_BLK = 0.0258  # TDM /8 block


def run(a_n, a_entries, a_t_create=0.0):
    clock = BlockClock(a_n_bufs=a_n, a_t_blk=T_BLK, a_t_create=a_t_create)
    return np.array([clock.refill(t) for t in a_entries])


def test_slow_loop_is_n_minus_1_stale():
    # Loop slower than block (always, in app): DMA idles, block from refill k was
    # queued at entry of refill k-n+1
    entries = 1.0 + 0.040 * np.arange(20)
    for n in (1, 2, 4):
        t = run(n, entries)
        k = np.arange(n + 2, 20)  # past the blocks queued at creation
        assert np.allclose(t[k], entries[k - n + 1] + T_BLK / 2), n


def test_first_blocks_are_from_creation():
    # All n blocks queued at creation, filled back to back
    t = run(4, 1.0 + 0.040 * np.arange(4))
    assert np.allclose(t, T_BLK * (np.arange(4) + 0.5))


def test_fast_loop_is_back_to_back():
    # Loop faster than block: DMA never idles, blocks T_BLK apart
    entries = 0.010 * np.arange(40)
    for n in (2, 4):
        t = run(n, entries)
        assert np.allclose(np.diff(t), T_BLK), n


def test_stamps_at_least_a_block_apart():
    # Mixed loop times: one DMA, blocks never overlap in time
    rng = np.random.default_rng(0)
    entries = np.cumsum(rng.uniform(0.005, 0.080, 500))
    for n in (1, 2, 4):
        assert np.all(np.diff(run(n, entries)) >= T_BLK - 1e-12), n


if __name__ == "__main__":
    tests = [
        test_slow_loop_is_n_minus_1_stale,
        test_first_blocks_are_from_creation,
        test_fast_loop_is_back_to_back,
        test_stamps_at_least_a_block_apart,
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
