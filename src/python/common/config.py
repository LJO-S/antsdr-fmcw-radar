from dataclasses import dataclass, field
import numpy as np

c = 3e8  # Speed of light in m/s


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

    MTI_EN: bool = False

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

    # CFAR
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
    SDR_TX_GAIN_DB: float = field(
        default=-30.0,
        metadata={
            "label": "Tx Gain",
            "unit": "dB",
            "scale": 1,
            "group": "sdr",
        },
    )
    SDR_RX_GAIN_MODE: str = "manual"  # untagged, must stay "manual"
    SDR_RX_GAIN_DB: float = field(
        default=20.0,
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
    # Miscellaneous
    # --------------------------------
    OP_RANGE_FACTOR: float = field(
        default=0.15,
        metadata={
            "label": "Operational Range Factor",
            "unit": "",
            "scale": 1,
            "group": "misc",
        },
    )

    # CHARACTERISTICS
    @property
    def MAX_RANGE(self) -> float:
        return self.OP_RANGE_FACTOR * (c * self.CHIRP_DUR_S) / 2

    @property
    def MAX_VELOCITY(self) -> float:
        t_rep = 2 * self.CHIRP_DUR_S if self.TRIANGLE_EN else self.CHIRP_DUR_S
        return (c / self.CHIRP_FC_HZ) / (4 * t_rep)

    def describe(self):
        print(f"FMCW Radar Characteristics:")
        print(f"  Chirp Center Frequency: {self.CHIRP_FC_HZ / 1e9} GHz")
        print(f"  Chirp Bandwidth: {self.CHIRP_BW_HZ/ 1e6} MHz")
        print(f"  Chirp Duration: {self.CHIRP_DUR_S * 1e6} us")
        print(f"  Theoretical Max Range: {(c * self.CHIRP_DUR_S) / 2} m")
        for name, (value, unit) in self.derived_params().items():
            print(f"\t{name}: {value:.1f} {unit}")

    def derived_params(self):
        r = {}
        r["Range Resolution"] = (c / (2 * self.CHIRP_BW_HZ), "m")
        r["Velocity Resolution"] = (
            (c / self.CHIRP_FC_HZ) / (2 * self.CHIRP_DUR_S * self.CHIRP_REPS),
            "m/s",
        )
        r["Operational Max Range"] = (self.MAX_RANGE, "m")
        r["Operational Max Velocity"] = (
            (self.MAX_VELOCITY if not self.TRIANGLE_EN else self.MAX_VELOCITY / 2),
            "m/s",
        )
        r["Processing Gain"] = (
            10 * np.log10(self.CHIRP_DUR_S * self.FS * self.CHIRP_REPS),
            "dB",
        )
        return r
