from dataclasses import dataclass
import numpy as np

c = 3e8  # Speed of light in m/s


@dataclass
class RadarConfig:
    """
    Configuration for FMCW radar
    """

    # Chirp parameters
    CHIRP_FC_HZ: float = 900e6
    CHIRP_BW_HZ: float = 50e6
    CHIRP_DUR_S: float = 100e-6
    CHIRP_REPS: int = 128

    # Frontend
    TX_PWR_DBM: float = 10.0
    TX_GAIN_DB: float = 13.0
    RX_GAIN_DB: float = 14.0

    # Sampling
    FS: float = 56.6e6
    TRIANGLE_EN: bool = True

    # CFAR
    CFAR_GUARD_LEN: int = 4
    CFAR_TRAINING_LEN: int = 10
    CFAR_PFA: float = 1e-6

    # Miscellaneuous
    OP_RANGE_FACTOR = 0.2

    # Hardware (approximations)
    ISOLATION_DB: float = 40.0
    DC_I: float = 0.0
    DC_Q: float = 0.0
    IQ_AMPL_ERR: float = 0.0
    IQ_PHASE_ERR_DEG: float = 0.0
    # IQ_AMPL_ERR: float = 1e-6
    # IQ_PHASE_ERR_DEG: float = 0.1

    print(f"FMCW Radar Characteristics:")
    print(f"  Chirp Center Frequency: {CHIRP_FC_HZ / 1e9} GHz")
    print(f"  Chirp Bandwidth: {CHIRP_BW_HZ/ 1e6} MHz")
    print(f"  Chirp Duration: {CHIRP_DUR_S * 1e6} us")
    print(f"  Range Resolution: {c / (2 * CHIRP_BW_HZ)} m")
    print(
        f"  Velocity Resolution: {(c/CHIRP_FC_HZ) / (2 * CHIRP_DUR_S * CHIRP_REPS)} m"
    )
    print(f"  Theoretical Max Range: {(c * CHIRP_DUR_S) / 2} m")
    print(f"  Operational Max Range: {OP_RANGE_FACTOR * (c * CHIRP_DUR_S) / 2} m")
    print(f"  Max Velocity: {(c/CHIRP_FC_HZ) / (4 * CHIRP_DUR_S)} m/s")
    print(f"Processing Gain: {10 * np.log10(CHIRP_DUR_S * FS * CHIRP_REPS)}")
