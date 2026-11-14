"""
Capture time of each RX block, from refill() entry times.

Kernel holds n RX blocks in FIFO (libiio v0.x local backend behind iiod). All n queued
at buffer creation; each refill() re-queues last handed-out block, returns oldest full
one. DMA fills queued blocks back to back, so block starts at max(its queue time, end of
previous block). Measured on board with RX gain step at n = 1, 2, 4 (+-1 ms):
.claude/rules/sdr.md.

Pure (no libiio), unit-tested board-free.
"""

from collections import deque


class BlockClock:
    def __init__(self, a_n_bufs: int, a_t_blk: float, a_t_create: float):
        # Queue times of blocks in kernel, oldest first
        self._queued = deque([a_t_create] * a_n_bufs)
        self._t_blk = a_t_blk
        self._end = a_t_create  # capture end of last block handed out
        self._holding = False  # caller holds a block, re-queued by next refill

    def refill(self, a_t_entry: float) -> float:
        """
        Call after successful refill() with its entry time. Returns capture midpoint
        of block refill() returned.
        """
        if self._holding:
            self._queued.append(a_t_entry)
        self._holding = True
        start = max(self._queued.popleft(), self._end)
        self._end = start + self._t_blk
        return start + 0.5 * self._t_blk
