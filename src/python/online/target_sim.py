import dataclasses
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
    theta0: float = field(
        default=0,
        metadata={
            "label": "Azimuth",
            "unit": "deg",
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
    def __init__(self, a_seed=None):
        self.fake_targets = []
        # Loopback noise source; a_seed makes it repeatable (never replay-exact)
        self.rng = np.random.default_rng(a_seed)

    def set_targets(self, a_targets: list):
        targets = []
        now = time.monotonic()
        for t in a_targets:
            t = FakeTarget(**t)
            t.t_spawn = now
            targets.append(t)
        self.fake_targets = targets

    def snapshot(self) -> list[dict]:
        """Targets incl. t_spawn, for session.json."""
        return [dataclasses.asdict(t) for t in self.fake_targets]

    def restore(self, a_snapshot: list[dict]):
        """Inverse of snapshot(): keeps the recorded t_spawn, unlike set_targets()."""
        self.fake_targets = [FakeTarget(**t) for t in a_snapshot]

    def advance(self, a_config: config.RadarConfig, a_now: float):
        """The state change apply()/apply_if() would make at a_now (an expired target
        restarts from a_now), without synthesizing any echo. Playback uses it to
        skip blocks and still land every target where it was live."""
        for target in self.fake_targets:
            self._kinematics(target, a_config, a_now)

    # a_now is the capture timestamp. Live it defaults to time.monotonic(); replay
    # passes the recorded one from index.csv, so the kinematics (and the expiry
    # reset of t_spawn) repeat exactly.
    def _kinematics(
        self, a_target: FakeTarget, a_config: config.RadarConfig, a_now: float = None
    ):
        now = time.monotonic() if a_now is None else a_now
        age = now - a_target.t_spawn
        if age > a_target.duration:
            a_target.t_spawn = now
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
        a_now: float = None,
    ) -> np.ndarray:
        # One timestamp for every target in the block
        now = time.monotonic() if a_now is None else a_now
        ret = a_if_raw.copy()

        N = a_ctx.N_if_samples
        # The reshape below needs a whole number of chirp legs (len % N == 0).
        rows = ret.reshape(-1, N)
        k = np.arange(len(rows))
        m = np.arange(N)
        fs = a_config.FS_IF

        # T_EFF, not CHIRP_DUR_S: SWEEP_LEN (and so T_EFF) shrinks by up to 7
        # samples under decimation, and B is what must stay exact (I5 guide
        # Section 6).
        S = a_config.CHIRP_BW_HZ / a_config.T_EFF
        rms = np.sqrt(np.mean(np.abs(a_if_raw) ** 2))
        rms = rms if rms > 0 else 1.0

        # TDM B rows: the second half of each period
        is_b = (k % a_config.LEGS_PER_PERIOD) >= a_config.LEGS_PER_PERIOD // 2

        for target in self.fake_targets:
            r, v = self._kinematics(target, a_config, now)

            # ---------------
            # Beat Frequency
            # ---------------
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

            # ---------------
            # Echo
            # ---------------
            # Per row: amp*rms * slow-time Doppler * TDM phase, times a per-sample
            # beat + fast-time Doppler tone: one outer product, no full-length exp.
            # Doppler is +f_dv: this writes the IF domain, past mix_signal's
            # conj(RX) flip (apply_doppler_shift's minus is for the RX domain).
            f_dv = 2 * v * a_config.CHIRP_FC_HZ / dsp.c
            row_vec = target.amp * rms * np.exp(2j * np.pi * f_dv * N * k / fs)
            if a_config.TDM_EN:
                # The geometry phase is 2*pi*d*sin(theta)/lam, d the antenna
                # separation and lam the wavelength.
                phi = (
                    2
                    * np.pi
                    * a_config.TDM_TX_ANT_SEP_M
                    * np.sin(np.radians(target.theta0))
                    / (dsp.c / (a_config.CHIRP_FC_HZ))
                )
                row_vec = np.where(is_b, row_vec * np.exp(-1j * phi), row_vec)

            # Phases in float64; outer product in the block's dtype.
            row_vec = row_vec.astype(ret.dtype)
            tone_up = np.exp(2j * np.pi * (f_b + f_dv) * m / fs).astype(ret.dtype)
            if a_config.TRIANGLE_EN:
                # Down-legs (odd rows) have slope -S, i.e. beat -f_b
                tone_down = np.exp(2j * np.pi * (f_dv - f_b) * m / fs)
                tone_down = tone_down.astype(ret.dtype)
                rows[0::2] += np.multiply.outer(row_vec[0::2], tone_up)
                rows[1::2] += np.multiply.outer(row_vec[1::2], tone_down)
            else:
                rows += np.multiply.outer(row_vec, tone_up)

        if a_config.SDR_LOOPBACK_EN:
            ret = dsp.apply_noise(
                a_signal=ret,
                a_snr_db=a_config.SDR_LOOPBACK_NOISE_SNR_DB,
                a_rng=self.rng,
            )

        return ret

    def apply(
        self, a_rx_raw: np.ndarray, a_config: config.RadarConfig, a_now: float = None
    ):
        now = time.monotonic() if a_now is None else a_now
        ret = a_rx_raw.copy()

        # Loop over targets
        for target in self.fake_targets:
            r, v = self._kinematics(target, a_config, now)

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
                a_signal=ret,
                a_snr_db=a_config.SDR_LOOPBACK_NOISE_SNR_DB,
                a_rng=self.rng,
            )

        return ret


if __name__ == "__main__":
    print("Not standalone. Run 'python -m online.app'")
