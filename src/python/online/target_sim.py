import logging
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
    nyquist_warned: bool = False


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

    def _kinematics(self, a_target: FakeTarget, a_config: config.RadarConfig):
        age = time.monotonic() - a_target.t_spawn
        if age > a_target.duration:
            a_target.t_spawn = time.monotonic()
            age = 0.0
        r = a_target.r0 + a_target.v0 * age + 0.5 * a_target.a0 * age**2
        r = np.clip(r, 0, a_config.MAX_RANGE)
        v = a_target.v0 + a_target.a0 * age
        v = np.clip(v, -a_config.MAX_VELOCITY, a_config.MAX_VELOCITY)
        return r, v

    def apply_if(
        self,
        a_if_raw: np.ndarray,
        a_config: config.RadarConfig,
        a_ctx: dsp.CPIContext,
    ) -> np.ndarray:
        ret = a_if_raw.copy()

        n = len(a_if_raw)
        N = a_ctx.N_if_samples
        # The beat tone is periodic: fast time resets every chirp, so one chirp
        # (one triangle period) of phasor tiles across the whole CPI. Building it
        # full-length instead would be ~CHIRP_REPS times the np.exp work and more memory.
        # The np.roll below is cheap and vectorized, so this is faster.
        # Decimated IF domain: N/fs_if, not N_chirp_samples/FS.
        t_chirp = np.arange(N) / a_config.FS_IF

        # T_EFF, not CHIRP_DUR_S: SWEEP_LEN (and so T_EFF) shrinks by up to 7
        # samples under decimation, and B is what must stay exact (I5 guide
        # Section 6).
        S = a_config.CHIRP_BW_HZ / a_config.T_EFF
        rms = np.sqrt(np.mean(np.abs(a_if_raw) ** 2))
        rms = rms if rms > 0 else 1.0

        for target in self.fake_targets:
            r, v = self._kinematics(target, a_config)

            f_b = S * 2 * r / dsp.c
            if abs(f_b) >= a_config.FS_IF / 2:
                if not target.nyquist_warned:
                    logging.getLogger(__name__).warning(
                        "apply_if: target at r=%.1fm has beat freq %.3f MHz "
                        ">= Nyquist (%.3f MHz), skipping",
                        r,
                        f_b / 1e6,
                        a_config.FS_IF / 2e6,
                    )
                    target.nyquist_warned = True
                continue
            target.nyquist_warned = False

            tone_up = np.exp(2j * np.pi * f_b * t_chirp).astype(a_if_raw.dtype)
            if a_config.TRIANGLE_EN:
                # Down-legs have slope -S, i.e. beat -f_b which is exactly the
                # conjugate of the up-leg tone.
                period = np.concatenate((tone_up, tone_up.conj()))
            else:
                period = tone_up
            tone = np.tile(period, -(-n // len(period)))[:n]

            echo = (target.amp * rms * tone).astype(a_if_raw.dtype, copy=False)

            # dsp.apply_doppler_shift's minus-sign convention is calibrated for
            # pre-mix RX-domain use (mix_signal's conj(RX) flips it to the correct
            # sign downstream). apply_if writes directly into the IF domain,
            # skipping that conjugation, so the velocity must be negated here to
            # land on the same measured-sign convention as the baseband path.
            echo = dsp.apply_doppler_shift(
                a_signal=echo, a_velocity=-v, a_config=a_config, a_fs=a_config.FS_IF
            )

            ret += echo

        if a_config.SDR_LOOPBACK_EN:
            ret = dsp.apply_noise(
                a_signal=ret, a_snr_db=a_config.SDR_LOOPBACK_NOISE_SNR_DB
            )

        return ret

    def apply(self, a_rx_raw: np.ndarray, a_config: config.RadarConfig):
        ret = a_rx_raw.copy()

        # Loop over targets
        for target in self.fake_targets:
            r, v = self._kinematics(target, a_config)

            # Convert into samples and rotate
            r_sample_offset = round(2 * r * a_config.FS / dsp.c)
            echo = target.amp * np.roll(a_rx_raw, r_sample_offset)

            # Apply doppler
            echo_w_doppler = dsp.apply_doppler_shift(
                a_signal=echo, a_velocity=v, a_config=a_config
            )

            # Sum
            ret += echo_w_doppler

        # Add noise if loopback enabled
        if a_config.SDR_LOOPBACK_EN:
            ret = dsp.apply_noise(
                a_signal=ret, a_snr_db=a_config.SDR_LOOPBACK_NOISE_SNR_DB
            )

        return ret


if __name__ == "__main__":
    print("Not standalone. Run 'python -m online.app'")
