from dataclasses import dataclass, field
import numpy as np

c = 3e8  # Speed of light in m/s

# Fixed in HDL (three cascaded halfband stages)
FABRIC_DECIMATE_RATIO = 8


@dataclass
class RadarConfig:
    """
    Configuration for FMCW radar
    """

    # --------------------------------
    # GLOBAL PARAMETERS
    # --------------------------------
    # Chirp
    CHIRP_FC_HZ: float = field(
        default=5800e6,
        metadata={"label": "Carrier LO", "unit": "MHz", "scale": 1e6, "group": "chirp"},
    )
    CHIRP_BW_HZ: float = field(
        default=50e6,
        metadata={"label": "BW", "unit": "MHz", "scale": 1e6, "group": "chirp"},
    )
    CHIRP_DUR_S: float = field(
        default=100e-6,
        metadata={"label": "Duration", "unit": "us", "scale": 1e-6, "group": "chirp"},
    )
    CHIRP_REPS: int = field(
        default=128,
        metadata={"label": "Chirps", "unit": "", "scale": 1, "group": "chirp"},
    )
    TRIANGLE_EN: bool = field(
        default=False,
        metadata={
            "label": "Triangle",
            "unit": "",
            "scale": 1,
            "group": "chirp",
        },
    )

    # --------------------------------
    # MTI
    # --------------------------------
    MTI_EN: bool = False

    # Fabric IF mode: fabric NCO transmits, fabric dechirps pre-DMA and ships the IF stream
    FABRIC_DECHIRP_EN: bool = field(
        default=False,
        metadata={"label": "Fabric Dechirp", "unit": "", "scale": 1, "group": "fabric"},
    )
    # Fabric /8 halfband decimation of the IF (only acts with FABRIC_DECHIRP_EN)
    FABRIC_DECIM_EN: bool = field(
        default=True,
        metadata={
            "label": "Fabric Decimation",
            "unit": "",
            "scale": 1,
            "group": "fabric",
        },
    )

    # --------------------------------
    # TDM
    # --------------------------------
    # 2T1R TDM: chirps alternate TX A, B (needs FABRIC_DECHIRP_EN).
    # With TRIANGLE_EN: A-up, A-down, B-up, B-down
    TDM_EN: bool = field(
        default=False,
        metadata={"label": "TDM (2T1R)", "unit": "", "scale": 1, "group": "fabric"},
    )
    # Antenna Tx separation in meters, fixed by the TX board: shown, not editable
    TDM_TX_ANT_SEP_M: float = field(
        default=0.0259,
        metadata={
            "label": "TX antenna spacing",
            "unit": "m",
            "scale": 1,
            "group": "angle",
            "readonly": True,
        },
    )
    ANGLE_PSI0_DEG: float = field(
        default=0.0,
        metadata={
            "label": "Phase Offset calibration",
            "unit": "deg",
            "scale": 1,
            "group": "angle",
        },
    )

    # --------------------------------
    # FABRIC
    # --------------------------------
    # TX->RX digital loopback latency in samples
    FABRIC_DECHIRP_DELAY: int = field(
        default=61,
        # default=34, # LOOPBACK
        metadata={
            "label": "Dechirp Delay",
            "unit": "samp",
            "scale": 1,
            "group": "fabric",
        },
    )
    # IF samples dropped from the front of each fabric block. 0 by construction; I5 step 8 confirms.
    FABRIC_FRAME_TRIM: int = field(
        default=0,
        metadata={
            "label": "Frame Trim",
            "unit": "samp",
            "scale": 1,
            "group": "fabric",
        },
    )

    # --------------------------------
    # CFAR
    # --------------------------------
    CFAR_GUARD_LEN: int = field(
        default=4,
        metadata={
            "label": "Guard Cells",
            "unit": "",
            "scale": 1,
            "group": "cfar",
        },
    )
    CFAR_TRAINING_LEN: int = field(
        default=5,
        metadata={
            "label": "Training Cells",
            "unit": "",
            "scale": 1,
            "group": "cfar",
        },
    )
    CFAR_PFA: float = field(
        default=1e-6,
        metadata={
            "label": "False Alarm Rate",
            "unit": "",
            "scale": 1,
            "group": "cfar",
        },
    )

    CFAR_MASK_N: int = field(
        default=5,
        metadata={
            "label": "Zero Bin Mask",
            "unit": "",
            "scale": 1,
            "group": "cfar",
        },
    )
    # Caps the range axis and the CFAR search (MAX_RANGE = min(this, EFFECTIVE_RANGE))
    MAX_RANGE_M: float = field(
        default=800,
        metadata={
            "label": "Max Range",
            "unit": "m",
            "scale": 1,
            "group": "cfar",
        },
    )

    # --------------------------------
    # Software
    # --------------------------------
    # GUI-untagged
    TX_PWR_DBM: float = 10.0
    TX_GAIN_DB: float = 13.0
    RX_GAIN_DB: float = 14.0
    ISOLATION_DB: float = 40.0
    DC_I: float = 0.0
    DC_Q: float = 0.0
    IQ_AMPL_ERR: float = 0.0
    IQ_PHASE_ERR_DEG: float = 0.0
    # IQ_AMPL_ERR: float = 1e-6
    # IQ_PHASE_ERR_DEG: float = 0.1

    # --------------------------------
    # Hardware
    # --------------------------------
    SDR_IP: str = field(
        default="192.168.5.10",
        metadata={
            "label": "IP",
            "unit": "",
            "scale": 1,
            "group": "sdr",
            "readonly": True,
        },
    )

    # Sampling
    FS: float = field(
        default=56.6e6,
        metadata={
            "label": "Sampling Frequency",
            "unit": "MHz",
            "scale": 1e6,
            "group": "sdr",
        },
    )

    # Start at -15 dB (~9dB under limit)
    SDR_TX_GAIN_DB: float = field(
        default=-60.0,
        metadata={
            "label": "Tx Gain",
            "unit": "dB",
            "scale": 1,
            "group": "sdr",
        },
    )
    SDR_TX_GAIN_MAX_DB: float = (
        0.00  # 0.8 dBm at Tx SMA assuming worstcase 7 dbm output @ 5.8 GHz
    )
    SDR_RX_GAIN_MODE: str = "manual"  # untagged, must stay "manual"
    SDR_RX_GAIN_DB: float = field(
        default=40.0,
        metadata={
            "label": "Rx Gain",
            "unit": "dB",
            "scale": 1,
            "group": "sdr",
        },
    )

    SDR_RX_MARGIN_PERIODS: int = 1  # untagged, I like 1 period

    SDR_LOOPBACK_EN: bool = field(
        default=True,
        metadata={
            "label": "Loopback",
            "unit": "",
            "scale": 1,
            "group": "sdr",
        },
    )
    SDR_LOOPBACK_NOISE_SNR_DB: float = field(
        default=1.0,
        metadata={
            "label": "Loopback SNR",
            "unit": "dB",
            "scale": 1,
            "group": "loopback",
        },
    )

    # --------------------------------
    # TRACKER
    # --------------------------------
    # Enable tracker
    TRACK_EN: bool = field(
        default=False,
        metadata={
            "label": "Tracker",
            "unit": "",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Confirm track if hits in M-of-N CPIs
    TRACK_CONFIRM_M: int = field(
        default=3,
        metadata={
            "label": "Confirm M(-of-N)",
            "unit": "",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Confirm track if hits in M-of-N CPIs
    TRACK_CONFIRM_N: int = field(
        default=4,
        metadata={
            "label": "Confirm (M-of)-N",
            "unit": "",
            "scale": 1,
            "group": "tracking",
        },
    )
    # "Newtonian Physics Rule Enforcement"
    # How many sigma a track's range slope may differ from its measured velocity before rejection
    # Higher k: fewer spurs + slower confirmation
    # Lower k: more spurs + faster confirmation
    TRACK_KINEMATIC_SIGMA: float = field(
        default=3.0,
        metadata={
            "label": "Kinematic Sigma",
            "unit": "sigma",
            "scale": 1,
            "group": "tracking",
        },
    )
    # History the kinematic check fits:
    # Longer: slower targets decided, slow spurs stay tentative longer.
    # Shorter: slow targets tentative, slow spurs stay tentative shorter.
    TRACK_KINEMATIC_WINDOW_S: float = field(
        default=3.0,
        metadata={
            "label": "Kinematic Window",
            "unit": "s",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Confirmed track with no update for this long is deleted
    TRACK_DELETE_S: float = field(
        default=0.5,
        metadata={
            "label": "Delete After",
            "unit": "s",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Tolerance time before suddenly stopped target is deleted (if MTI_EN)
    TRACK_STOP_COAST_S: float = field(
        default=5.0,
        metadata={
            "label": "Stopped Coast",
            "unit": "s",
            "scale": 1,
            "group": "tracking",
        },
    )
    # How many CPIs before two tracks wtihin one gate merges
    TRACK_MERGE_N: int = field(
        default=10,
        metadata={
            "label": "Merge After",
            "unit": "CPIs",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Max tracked objects
    TRACK_MAX: int = field(
        default=100,
        metadata={
            "label": "Max Tracks",
            "unit": "",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Minimum |v| to start a track (0 = off) (i.e  if MTI_EN=off)
    TRACK_MIN_SPEED: float = field(
        default=0.0,
        metadata={
            "label": "Min Start Speed",
            "unit": "m/s",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Process noise: std of the random acceleration
    TRACK_STD_A: float = field(
        default=5.0,
        metadata={
            "label": "Accel. Std",
            "unit": "m/s^2",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Measurement noise: range std (sub-bin refined; bins are ~3 m)
    TRACK_STD_R: float = field(
        default=0.7,
        metadata={
            "label": "Range Std",
            "unit": "m",
            "scale": 1,
            "group": "tracking",
        },
    )
    # Measurement noise: velocity std
    TRACK_STD_V: float = field(
        default=0.5,
        metadata={
            "label": "Velocity Std",
            "unit": "m/s",
            "scale": 1,
            "group": "tracking",
        },
    )
    # --------------------------------
    # Miscellaneous
    # --------------------------------

    # CHARACTERISTICS
    @property
    def _decimating(self) -> bool:
        # Decimation only exists downstream of the fabric dechirp path.
        return self.FABRIC_DECHIRP_EN and self.FABRIC_DECIM_EN

    @property
    def FS_IF(self) -> float:
        return self.FS / FABRIC_DECIMATE_RATIO if self._decimating else self.FS

    @property
    def SWEEP_LEN(self) -> int:
        # Rounded to a multiple of the ratio so N_IF is exact, not a floor
        # Costs at most 7 samples of sweep time.
        n = round(self.CHIRP_DUR_S * self.FS)
        if self._decimating:
            n -= n % FABRIC_DECIMATE_RATIO
        return n

    @property
    def T_EFF(self) -> float:
        return self.SWEEP_LEN / self.FS

    @property
    def N_IF(self) -> int:
        ratio = FABRIC_DECIMATE_RATIO if self._decimating else 1
        return self.SWEEP_LEN // ratio

    @property
    def EFFECTIVE_RANGE(self) -> float:
        return 0.4 * self.FS_IF * c * self.T_EFF / (2 * self.CHIRP_BW_HZ)

    @property
    def MAX_RANGE(self) -> float:
        return min(self.MAX_RANGE_M, self.EFFECTIVE_RANGE)

    @property
    def LEGS_PER_PERIOD(self) -> int:
        return (2 if self.TRIANGLE_EN else 1) * (2 if self.TDM_EN else 1)

    @property
    def MAX_VELOCITY(self) -> float:
        t_rep = self.LEGS_PER_PERIOD * self.T_EFF
        return (c / self.CHIRP_FC_HZ) / (4 * t_rep)

    def describe(self):
        print(f"FMCW Radar Characteristics:")
        print(f"  Chirp Center Frequency: {self.CHIRP_FC_HZ / 1e9} GHz")
        print(f"  Chirp Bandwidth: {self.CHIRP_BW_HZ/ 1e6} MHz")
        print(f"  Chirp Duration: {self.CHIRP_DUR_S * 1e6} us")
        for name, (value, unit) in self.derived_params().items():
            print(f"\t{name}: {value:.1f} {unit}")

    def derived_params(self):
        r = {}
        r["Range Resolution"] = (c / (2 * self.CHIRP_BW_HZ), "m")
        r["Velocity Resolution"] = (
            (c / self.CHIRP_FC_HZ)
            / (2 * self.LEGS_PER_PERIOD * self.T_EFF * self.CHIRP_REPS),
            "m/s",
        )
        r["Operational Max Range"] = (self.MAX_RANGE, "m")
        r["Operational Max Velocity"] = (self.MAX_VELOCITY, "m/s")
        r["Processing Gain"] = (
            10 * np.log10(self.CHIRP_DUR_S * self.FS * self.CHIRP_REPS),
            "dB",
        )
        r["IF Sample Rate"] = (self.FS_IF / 1e6, "MHz")
        r["IF Samples / CPI leg"] = (self.N_IF, "")
        r["Effective Range"] = (self.EFFECTIVE_RANGE, "m")
        return r
