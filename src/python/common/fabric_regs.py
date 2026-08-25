"""FMC1 register encoding for fmcw_core (Phase 4 chirp NCO).

Pure math, no I/O, no radio. Formulas are docs/AntSDR_Phase4_Chirp_NCO_TX_Guide.md
Section 4.2 - read that before changing anything here, especially the `+ s/2`
term in `chirp_ftw`, which is not a fudge factor (see the guide).
"""

from .config import RadarConfig

# Register offsets (FMC1 map, guide Section 2)
REG_MAGIC = 0x00
REG_CTRL = 0x04
REG_SCRATCH = 0x08
REG_COUNT = 0x0C
REG_FTW_START = 0x10
REG_FTW_SLOPE = 0x14
REG_SWEEP_LEN = 0x18
REG_CHIRP_COUNT = 0x1C
REG_COMMIT = 0x20
REG_DECHIRP_DELAY = 0x24  # reserved, Phase 5
REG_DECIM_SEL = 0x28  # reserved, Phase 5
REG_IF_SEL = 0x2C  # reserved, Phase 5

MAGIC = 0x464D4331  # "FMC1"

CTRL_RAMP_EN = 1 << 0
CTRL_NCO_EN = 1 << 1
CTRL_TX_SRC = 1 << 2
CTRL_TRIANGLE_EN = 1 << 3
CTRL_RX_DBG_MUX = 1 << 4
CTRL_SYNC_SRC = 1 << 5


def _u32(value: int) -> int:
    """Two's-complement encode a signed int into an unsigned 32-bit register word."""
    return value & 0xFFFFFFFF


def chirp_ftw(bw_hz: float, dur_s: float, fs_hz: float) -> tuple[int, int, int]:
    """FTW_START, FTW_SLOPE, SWEEP_LEN for a -bw/2 -> +bw/2 sweep over dur_s at fs_hz.

    Guide 4.1/4.2: FTW lives on the half-sample grid, phase on the sample grid.
    The `+ s/2` term corrects for that - omitting it delays the fabric chirp by
    exactly half a sample (0.44 range bins at the radar's own defaults).
    Returned words are two's-complement-encoded (unsigned 0..2**32-1), ready to
    write into FTW_START/FTW_SLOPE.
    """
    slope = bw_hz / (dur_s * fs_hz**2)  # cycles / sample^2
    ftw_start = round((-bw_hz / (2 * fs_hz) + slope / 2) * 2**32)
    ftw_slope = round(slope * 2**32)
    sweep_len = round(dur_s * fs_hz)
    return _u32(ftw_start), _u32(ftw_slope), sweep_len


def register_image(cfg: RadarConfig) -> dict[int, int]:
    """RadarConfig -> {offset: value} for FTW_START/FTW_SLOPE/SWEEP_LEN + CTRL.

    Does not set COMMIT or nco_en - those are sequencing (write this image,
    then COMMIT, then enable), not persistent config state.
    """
    ftw_start, ftw_slope, sweep_len = chirp_ftw(cfg.CHIRP_BW_HZ, cfg.CHIRP_DUR_S, cfg.FS)
    return {
        REG_FTW_START: ftw_start,
        REG_FTW_SLOPE: ftw_slope,
        REG_SWEEP_LEN: sweep_len,
        REG_CTRL: CTRL_TRIANGLE_EN if cfg.TRIANGLE_EN else 0,
    }
