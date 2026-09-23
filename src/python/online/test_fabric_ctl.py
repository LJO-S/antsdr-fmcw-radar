"""
online/test_fabric_ctl.py

No-board tests for FabricCtl register-write sequencing. A RecordingTransport
stands in for SshDevmem: write_seq appends to a flat, ordered list and read
returns a canned MAGIC/STATUS, so every ordering invariant fabric_ctl.py's
module docstring documents can be asserted without a radio in sight.
"""

import dataclasses

import common.config as config
import common.fabric_regs as fabric_regs
from online.fabric_ctl import FabricCtl

TEST_DECHIRP_DELAY = 34


class RecordingTransport:
    """write_seq appends to `writes` in order; read() returns canned values."""

    def __init__(self, a_magic=fabric_regs.MAGIC, a_status_ok=True):
        self.writes: list[tuple[int, int]] = []
        self.magic = a_magic
        self.status = fabric_regs.STATUS_DAC_EN0 if a_status_ok else 0

    def write_seq(self, a_pairs):
        self.writes.extend(a_pairs)

    def read(self, a_offset):
        if a_offset == fabric_regs.REG_MAGIC:
            return self.magic
        if a_offset == fabric_regs.REG_STATUS:
            return self.status
        raise KeyError(f"RecordingTransport has no canned read for 0x{a_offset:02X}")


def fabric_cfg(a_triangle_en: bool = False) -> config.RadarConfig:
    # RadarConfig is immutable by convention (CLAUDE.md) - never mutate the
    # default instance, build a fresh one via dataclasses.replace.
    return dataclasses.replace(
        config.RadarConfig(),
        FABRIC_DECHIRP_EN=True,
        FABRIC_DECHIRP_DELAY=TEST_DECHIRP_DELAY,
        TRIANGLE_EN=a_triangle_en,
    )


def offsets(pairs):
    return [o for o, _ in pairs]


def run_enable(cfg):
    transport = RecordingTransport()
    image = fabric_regs.register_image(cfg)
    FabricCtl(transport).enable(image)
    return transport, image


def check_enable_order(transport, image):
    """
    Shared assertions for both the sawtooth and triangle enable() cases.
    Returns (pre_ctrl, post_ctrl) so callers can add mode-specific checks.
    """
    offs = offsets(transport.writes)

    def idx(reg):
        return [i for i, o in enumerate(offs) if o == reg]

    commit_idx = idx(fabric_regs.REG_COMMIT)
    if_sel_idx = idx(fabric_regs.REG_IF_SEL)
    decim_idx = idx(fabric_regs.REG_DECIM_SEL)
    ctrl_idx = idx(fabric_regs.REG_CTRL)
    sweep_idx = idx(fabric_regs.REG_SWEEP_LEN)
    ftw_start_idx = idx(fabric_regs.REG_FTW_START)
    ftw_slope_idx = idx(fabric_regs.REG_FTW_SLOPE)
    delay_idx = idx(fabric_regs.REG_DECHIRP_DELAY)

    assert len(commit_idx) == 1, "enable() must COMMIT exactly once"
    assert len(if_sel_idx) == 1, "enable() must write IF_SEL exactly once"
    assert len(decim_idx) == 1, "enable() must write DECIM_SEL exactly once"
    assert len(ctrl_idx) == 2, "enable() writes CTRL twice: pre-COMMIT, then nco_en"

    # IF_SEL up LAST - after both CTRL words AND after DECIM_SEL - carrying the
    # image's own value (a bare position check would pass a stray IF_SEL=0
    # that never opens the IF path onto the capture side).
    assert if_sel_idx[0] > ctrl_idx[0] and if_sel_idx[0] > ctrl_idx[1], (
        "IF_SEL must be written after both CTRL words"
    )
    assert if_sel_idx[0] > decim_idx[0], "IF_SEL must be written after DECIM_SEL"
    assert (
        transport.writes[if_sel_idx[0]][1] == image[fabric_regs.REG_IF_SEL]
    ), "IF_SEL must carry the image's value, not just land in the right slot"

    # DECIM_SEL is live (no COMMIT dependency) but must land between nco_en and
    # IF_SEL, and carry the image's own value.
    assert decim_idx[0] > ctrl_idx[1], "DECIM_SEL must follow the nco_en CTRL word"
    assert (
        transport.writes[decim_idx[0]][1] == image[fabric_regs.REG_DECIM_SEL]
    ), "DECIM_SEL must carry the image's value, not just land in the right slot"

    # Shadow registers (FTW_START/FTW_SLOPE/SWEEP_LEN/DECHIRP_DELAY) precede
    # COMMIT and carry the image's values verbatim.
    assert ftw_start_idx and ftw_start_idx[0] < commit_idx[0]
    assert ftw_slope_idx and ftw_slope_idx[0] < commit_idx[0]
    assert transport.writes[ftw_start_idx[0]][1] == image[fabric_regs.REG_FTW_START]
    assert transport.writes[ftw_slope_idx[0]][1] == image[fabric_regs.REG_FTW_SLOPE]

    assert sweep_idx and sweep_idx[0] < commit_idx[0], "SWEEP_LEN must precede COMMIT"
    sweep_val = transport.writes[sweep_idx[0]][1]
    assert sweep_val != 0, "SWEEP_LEN=0 wedges the core ~76 s"

    assert delay_idx and delay_idx[0] < commit_idx[0], "DECHIRP_DELAY must precede COMMIT"
    assert transport.writes[delay_idx[0]][1] == TEST_DECHIRP_DELAY

    # Pre-COMMIT CTRL is triangle_en-or-nothing - NOT just "no nco_en". A
    # stray tx_src/sync_src bit here would still pass a looser "no nco_en"
    # check but is still wrong (those only matter latched with nco_en).
    pre_ctrl = transport.writes[ctrl_idx[0]][1]
    assert pre_ctrl & ~fabric_regs.CTRL_TRIANGLE_EN == 0, (
        "pre-COMMIT CTRL must carry only triangle_en"
    )

    # Post-COMMIT CTRL is exactly the image's CTRL word plus nco_en - not an
    # independently-built word that could silently drop tx_src/sync_src.
    post_ctrl = transport.writes[ctrl_idx[1]][1]
    assert post_ctrl == image[fabric_regs.REG_CTRL] | fabric_regs.CTRL_NCO_EN

    # sync_src never appears in a CTRL word without nco_en (hangs RX capture).
    # (Vacuous when sync_src is absent entirely - the post_ctrl equality check
    # above is what actually guarantees sync_src is present when expected.)
    for o, v in transport.writes:
        if o == fabric_regs.REG_CTRL and v & fabric_regs.CTRL_SYNC_SRC:
            assert v & fabric_regs.CTRL_NCO_EN, "sync_src set without nco_en"

    return pre_ctrl, post_ctrl


def test_enable_order():
    cfg = fabric_cfg(a_triangle_en=False)
    transport, image = run_enable(cfg)
    pre_ctrl, post_ctrl = check_enable_order(transport, image)

    assert image[fabric_regs.REG_CTRL] & fabric_regs.CTRL_TX_SRC
    assert image[fabric_regs.REG_CTRL] & fabric_regs.CTRL_SYNC_SRC
    assert not pre_ctrl & fabric_regs.CTRL_TRIANGLE_EN
    assert not post_ctrl & fabric_regs.CTRL_TRIANGLE_EN


def test_enable_triangle_bit():
    # triangle_en is NOT quasi-static despite its own synchronizer: it only
    # takes effect on a COMMIT, so it must be written in the PRE-COMMIT CTRL
    # word too, not just carried into the post-COMMIT one. This is the rule
    # that cost a hardware debugging session (CLAUDE.md) - assert it directly
    # instead of trusting the pre-COMMIT "nothing else" check to catch it.
    cfg = fabric_cfg(a_triangle_en=True)
    transport, image = run_enable(cfg)
    pre_ctrl, post_ctrl = check_enable_order(transport, image)

    assert pre_ctrl & fabric_regs.CTRL_TRIANGLE_EN, "triangle_en missing pre-COMMIT"
    assert post_ctrl & fabric_regs.CTRL_TRIANGLE_EN, "triangle_en missing post-COMMIT"


def test_disable_order():
    transport = RecordingTransport()
    FabricCtl(transport).disable()

    offs = offsets(transport.writes)
    assert offs[0] == fabric_regs.REG_IF_SEL, "IF_SEL must go down FIRST"
    assert transport.writes[0][1] == 0, "IF_SEL must be cleared, not just reordered"

    assert offs[1] == fabric_regs.REG_DECIM_SEL, "DECIM_SEL must follow IF_SEL"
    assert (
        transport.writes[1][1] == fabric_regs.DECIM_SEL_PASSTHROUGH
    ), "DECIM_SEL must be cleared to passthrough"

    ctrl_i = offs.index(fabric_regs.REG_CTRL)
    assert transport.writes[ctrl_i][1] == 0, "CTRL must clear fully"
    assert offs[-1] == fabric_regs.REG_COMMIT, "COMMIT must be written last"


def test_register_image_software_mode_no_fabric_regs():
    # Software mode (FABRIC_DECHIRP_EN=False) must not emit DECIM_SEL/IF_SEL/
    # DECHIRP_DELAY - those only exist in fabric IF mode.
    image = fabric_regs.register_image(config.RadarConfig())
    assert fabric_regs.REG_DECIM_SEL not in image
    assert fabric_regs.REG_IF_SEL not in image
    assert fabric_regs.REG_DECHIRP_DELAY not in image


def test_ramp_en_rejected():
    transport = RecordingTransport()
    ctl = FabricCtl(transport)
    image = fabric_regs.register_image(fabric_cfg())
    image[fabric_regs.REG_CTRL] |= fabric_regs.CTRL_RAMP_EN

    try:
        ctl.enable(image)
    except ValueError:
        pass
    else:
        raise AssertionError("enable() must reject an image with ramp_en set")

    assert not transport.writes, "no writes should happen once ramp_en is rejected"


def test_ramp_en_absent_from_normal_image():
    image = fabric_regs.register_image(fabric_cfg())
    assert not image.get(fabric_regs.REG_CTRL, 0) & fabric_regs.CTRL_RAMP_EN, (
        "register_image() must never set ramp_en itself"
    )


def test_set_calib_delay():
    transport = RecordingTransport()
    FabricCtl(transport).set_calib_delay(TEST_DECHIRP_DELAY)

    offs = offsets(transport.writes)
    assert offs == [fabric_regs.REG_DECHIRP_DELAY, fabric_regs.REG_COMMIT]
    assert transport.writes[0][1] == TEST_DECHIRP_DELAY
    assert fabric_regs.REG_CTRL not in offs, "set_calib_delay must never touch CTRL"


def test_set_calib_delay_range_check():
    transport = RecordingTransport()
    ctl = FabricCtl(transport)
    try:
        ctl.set_calib_delay(fabric_regs.DELAY_LINE_MAX)
    except ValueError:
        pass
    else:
        raise AssertionError("set_calib_delay must reject a delay >= DELAY_LINE_MAX")
    assert not transport.writes, "a rejected delay must write nothing"


def test_check_magic():
    ok = RecordingTransport(a_magic=fabric_regs.MAGIC)
    FabricCtl(ok).check_magic()  # must not raise

    bad = RecordingTransport(a_magic=0xDEADBEEF)
    try:
        FabricCtl(bad).check_magic()
    except RuntimeError:
        pass
    else:
        raise AssertionError("check_magic() must raise on a MAGIC mismatch")


def test_status_check():
    transport = RecordingTransport(a_status_ok=False)
    ctl = FabricCtl(transport)
    image = fabric_regs.register_image(fabric_cfg())
    try:
        ctl.enable(image)
    except RuntimeError:
        pass
    else:
        raise AssertionError("enable() must raise when STATUS.dac_enable_i0 is clear")


if __name__ == "__main__":
    tests = [
        test_enable_order,
        test_enable_triangle_bit,
        test_disable_order,
        test_register_image_software_mode_no_fabric_regs,
        test_ramp_en_rejected,
        test_ramp_en_absent_from_normal_image,
        test_set_calib_delay,
        test_set_calib_delay_range_check,
        test_check_magic,
        test_status_check,
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
