import time
import numpy as np
from dataclasses import dataclass, field
import common.dsp as dsp
import common.config as config


@dataclass
class FakeTarget:
    r0: float = field(
        default=500,
        metadata={"label": "Range", "unit": "m", "scale": 1, "group": "sim_target"},
    )
    v0: float = field(
        default=0,
        metadata={
            "label": "Velocity",
            "unit": "m/s",
            "scale": 1,
            "group": "sim_target",
        },
    )
    a0: float = field(
        default=0,
        metadata={
            "label": "Acceleration",
            "unit": "m/s2",
            "scale": 1,
            "group": "sim_target",
        },
    )
    amp: float = field(
        default=1,
        metadata={
            "label": "Amplitude",
            "unit": "",
            "scale": 0.01,
            "group": "sim_target",
        },
    )
    duration: float = field(
        default=10,
        metadata={
            "label": "Duration",
            "unit": "s",
            "scale": 10,
            "group": "sim_target",
        },
    )
    t_spawn: float = 0.0


class TargetSim:
    def __init__(self):
        self.fake_targets = []

    def set_targets(self, a_targets: list):
        targets = []
        now = time.monotonic()
        for t in a_targets:
            t = FakeTarget(**t)
            t.t_spawn = now
            targets.append(t)
        self.fake_targets = targets

    def apply(self, a_rx_raw: np.ndarray, a_config: config.RadarConfig):
        ret = a_rx_raw.copy()

        # Loop over targets
        for target in self.fake_targets:
            # Calculate current rng/vel values
            age = time.monotonic() - target.t_spawn
            if age > target.duration:
                target.t_spawn = time.monotonic()
                age = 0.0
            r = target.r0 + target.v0 * age + 0.5 * target.a0 * age**2
            r = np.clip(r, 0, a_config.MAX_RANGE)
            v = target.v0 + target.a0 * age
            v = np.clip(v, -a_config.MAX_VELOCITY, a_config.MAX_VELOCITY)

            # Convert into samples and rotate
            r_sample_offset = round(2 * r * a_config.FS / dsp.c)
            echo = target.amp * np.roll(a_rx_raw, r_sample_offset)

            # Apply doppler
            echo_w_doppler = dsp.apply_doppler_shift(
                a_signal=echo, a_velocity=v, a_config=a_config
            )

            # Sum
            ret += echo_w_doppler

        # Add noise
        ret = dsp.apply_noise(a_signal=ret, a_snr_db=a_config.SDR_LOOPBACK_NOISE_SNR_DB)

        return ret


if __name__ == "__main__":
    print("Not standalone. Run 'python -m online.app'")
