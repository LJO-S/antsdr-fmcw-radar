"""
fmcw_core register sequencing

Rules:
  - nco_en never before COMMIT      (SWEEP_LEN=0 wedges the core ~76 s)
  - sync_src never while nco_en=0   (no chirp_start pulses -> DMA waits forever)
  - IF_SEL up LAST, down FIRST      (while IF_SEL=1 the ADC valid is gated by
                                     the NCO, so an in-flight refill() starves)
  - ramp_en never                   (outranks IF_SEL in the ADC output mux)
"""

import subprocess

from common import fabric_regs


class SshDevmem:
    """
    One ssh call per write sequence: devmem at base + offset, &&-chained.

    Session setup dominates (~100-300 ms per ssh), so a whole enable sequence
    costs one round trip instead of eight. Needs key auth to root@<ip>
    (`ssh-copy-id root@192.168.5.10`, board password "analog") - BatchMode makes
    a missing key fail immediately instead of hanging on a password prompt
    inside the worker thread.
    """

    def __init__(self, a_ip: str, a_base: int = fabric_regs.FMCW_CORE_BASE):
        self.ip = a_ip
        self.base = a_base

    def _ssh(self, a_cmd: str) -> str:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                f"root@{self.ip}",
                a_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ssh cmd failed: {a_cmd}\nstderr: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def write_seq(self, a_pairs: list[tuple[int, int]]) -> None:
        """
        Write a sequence of (offset, value) pairs to the fabric registers.

        The pairs execute in order in a single remote shell - `&&` also aborts
        the rest of the sequence if any devmem fails, so a half-applied enable
        stops before nco_en rather than after it.
        """
        # busybox devmem wants the access width in BITS, not a letter.
        cmd = " && ".join(
            f"devmem 0x{self.base + off:08X} 32 0x{val & 0xFFFFFFFF:08X}"
            for off, val in a_pairs
        )
        self._ssh(cmd)

    def read(self, a_offset: int) -> int:
        """
        Read a single fabric register at the given offset.
        """
        return int(self._ssh(f"devmem 0x{self.base + a_offset:08X} 32"), 0)


class FabricCtl:
    """
    Enable / disable sequencing for fmcw_core over an injectable transport.

    The transport is anything with `write_seq(pairs)` and `read(offset)`; a
    recording implementation makes the write ORDER unit-testable without a board.
    """

    def __init__(self, a_transport):
        self.transport = a_transport

    def check_magic(self) -> None:
        """
        Verify the FPGA image matches this register map. Raises on mismatch:
        continuing past it means writing a stale map into live hardware.
        """
        magic = self.transport.read(fabric_regs.REG_MAGIC)
        if magic != fabric_regs.MAGIC:
            raise RuntimeError(
                f"MAGIC mismatch: read 0x{magic:08X}, expected "
                f"0x{fabric_regs.MAGIC:08X}. Wrong firmware image?"
            )

    def enable(self, a_image: dict[int, int]) -> None:
        """
        Bring up the fabric from the register image produced by
        `fabric_regs.register_image(cfg)`.
        """
        ctrl = a_image.get(fabric_regs.REG_CTRL, 0)
        if ctrl & fabric_regs.CTRL_RAMP_EN:
            # Not an assert: -O strips those, and this one guards against
            # silently replacing the IF stream with the debug counter.
            raise ValueError(
                f"image CTRL 0x{ctrl:08X} sets ramp_en - it outranks IF_SEL in "
                "the ADC output mux and would replace the IF stream"
            )

        # Shadow registers first; they latch together on COMMIT.
        seq = [
            (o, a_image[o])
            for o in [
                fabric_regs.REG_FTW_START,
                fabric_regs.REG_FTW_SLOPE,
                fabric_regs.REG_SWEEP_LEN,
                fabric_regs.REG_DECHIRP_DELAY,
            ]
            if o in a_image
        ]
        seq += [
            (fabric_regs.REG_CTRL, ctrl & fabric_regs.CTRL_TRIANGLE_EN),
            (fabric_regs.REG_COMMIT, 1),
            (fabric_regs.REG_CTRL, ctrl | fabric_regs.CTRL_NCO_EN),
            # IF_SEL LAST.
            (fabric_regs.REG_IF_SEL, a_image.get(fabric_regs.REG_IF_SEL, 0)),
        ]

        self.transport.write_seq(seq)

        # Separate round trip on purpose: it is the diagnostic AND the settle
        # margin the first refill() after a cold enable needs (without it the
        # flush can catch the DMA sync chain still spinning up and time out).
        status = self.transport.read(fabric_regs.REG_STATUS)
        if not status & fabric_regs.STATUS_DAC_EN0:
            raise RuntimeError(
                f"STATUS=0x{status:08X}: dac_enable_i0 clear - dac_data_sel is "
                "not DMA, the NCO output is being discarded at the DAC mux"
            )

    def disable(self) -> None:
        """
        Restore passthrough: IF_SEL down FIRST, then NCO off, then COMMIT.
        """
        self.transport.write_seq(
            [
                (fabric_regs.REG_IF_SEL, 0),
                (fabric_regs.REG_CTRL, 0),
                (fabric_regs.REG_COMMIT, 1),
            ]
        )

    def set_calib_delay(self, a_delay: int) -> None:
        """
        Reload the dechirp delay line only
        """
        if not 0 <= a_delay < fabric_regs.DELAY_LINE_MAX:
            raise ValueError(
                f"FABRIC_DECHIRP_DELAY {a_delay} outside "
                f"[0, {fabric_regs.DELAY_LINE_MAX}) - fabric delay line would wrap"
            )
        self.transport.write_seq(
            [
                (fabric_regs.REG_DECHIRP_DELAY, a_delay),
                (fabric_regs.REG_COMMIT, 1),
            ]
        )


if __name__ == "__main__":
    # Transport smoke test: proves ssh keys, root@, the base address and the
    # devmem syntax at once. Touches nothing, needs no radio.
    from common.config import RadarConfig

    transport = SshDevmem(RadarConfig().SDR_IP)
    for name, offset in [
        ("MAGIC", fabric_regs.REG_MAGIC),
        ("CTRL", fabric_regs.REG_CTRL),
        ("STATUS", fabric_regs.REG_STATUS),
        ("CHIRP_COUNT", fabric_regs.REG_CHIRP_COUNT),
    ]:
        print(f"  {name:12s} 0x{transport.read(offset):08X}")
    FabricCtl(transport).check_magic()
    print("  MAGIC OK")
