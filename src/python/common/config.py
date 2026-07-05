from dataclasses import dataclass
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
    CHIRP_FC_HZ: float = 900e6
    CHIRP_BW_HZ: float = 50e6
    CHIRP_DUR_S: float = 100e-6
    CHIRP_REPS: int = 32

    # Sampling
    FS: float = 56.6e6
    TRIANGLE_EN: bool = False

    # CFAR
    CFAR_GUARD_LEN: int = 4
    CFAR_TRAINING_LEN: int = 10
    CFAR_PFA: float = 1e-6

    # --------------------------------
    # Software
    # --------------------------------
    TX_PWR_DBM: float = 10.0
    TX_GAIN_DB: float = 13.0
    RX_GAIN_DB: float = 14.0

    # --------------------------------
    # Hardware
    # --------------------------------
    SDR_IP: str = "192.168.5.10"
    SDR_TX_GAIN_DB: float = -30.0
    SDR_RX_GAIN_MODE: str = "manual"
    SDR_RX_GAIN_DB: float = 20.0
    SDR_RX_MARGIN_PERIODS: int = 1
    SDR_LOOPBACK_EN: bool = True
    SDR_LOOPBACK_DELAY_SAMPLES: int = 600  # ~ c*D/(2*fs)
    SDR_LOOPBACK_VELOCITY_MPS: float = 10.0
    SDR_LOOPBACK_NOISE_SNR_DB: float = 5.0

    # --------------------------------
    # Miscellaneous
    # --------------------------------
    OP_RANGE_FACTOR: float = 0.15

    # --------------------------------
    # Software (approximations)
    # --------------------------------
    ISOLATION_DB: float = 40.0
    DC_I: float = 0.0
    DC_Q: float = 0.0
    IQ_AMPL_ERR: float = 0.0
    IQ_PHASE_ERR_DEG: float = 0.0
    # IQ_AMPL_ERR: float = 1e-6
    # IQ_PHASE_ERR_DEG: float = 0.1

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
        print(f"  Range Resolution: {c / (2 * self.CHIRP_BW_HZ)} m")
        print(
            f"  Velocity Resolution: {(c/self.CHIRP_FC_HZ) / (2 * self.CHIRP_DUR_S * self.CHIRP_REPS)} m"
        )
        print(f"  Theoretical Max Range: {(c * self.CHIRP_DUR_S) / 2} m")
        print(f"  Operational Max Range: {self.MAX_RANGE} m")
        print(f"  Max Velocity: {self.MAX_VELOCITY} m/s")
        print(
            f"Processing Gain: {10 * np.log10(self.CHIRP_DUR_S * self.FS * self.CHIRP_REPS)}"
        )


# TODO
#   One config.py heads-up for later:
# MAX_RANGE, MAX_VELOCITY and the rest sit directly in the class body and execute once at import time using the default values. A reconfigured instance with a new CHIRP_DUR_S will still report the old MAX_RANGE. Turn them
#   into @property methods (and move the printout into a def describe(self)).
