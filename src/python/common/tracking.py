"""
Radar tracker internals.

Detections = target dicts from process_cpi.
"""

import math
import numpy as np
from collections import deque
from dataclasses import dataclass
from scipy.optimize import linear_sum_assignment

from common.config import c, RadarConfig
from common.dsp import NMS_SIZE

# chi-square, 99 %
GATE_2DOF = 9.21
GATE_3DOF = 11.34

SV_ACROSS = 10.0  # init: std of velocity across line of sight [m/s]
SNR_NOMINAL_DB = 13.0  # R fallback when detection has no usable snr_db (~4 deg)
RANGE_MIN = 1e-6  # m, floor on EKF radial range: no div by zero at radar


def safe_range(a_x, a_y):
    """
    Radial range sqrt(x^2 + y^2) floored at RANGE_MIN. Scalars or arrays
    """
    return np.maximum(np.hypot(a_x, a_y), RANGE_MIN)


OUT_OF_GATE = 1e9  # assignment cost of pair outside gate
CONFIRMED = "confirmed"  # Track.status values
TENTATIVE = "tentative"
HISTORY_CAP = 500  # safety cap on history deque
# Pass needs sigma_d < |v| / (KIN_MARGIN * k), sigma_d = hypot(sigma_slope, sigma_vmean)
KIN_MARGIN = 2.0

#
#   __init__(cfg), init(det)      start from one detection
#   predict(dt)                   dt seconds ahead
#   innovation(det)               -> (y, S)
#   update(det)                   fold detection in
#   state, cov                    for GUI and tests
#
# Batched classmethods = one pass over all tracks:
#   predict_batch(filters, dt)
#   update_batch(filters, dets)   -> (v, r, nis) per pair, for the history
#   d2_matrix(filters, dets)      -> (n_f, n_d) d^2, for gating
#   pair_d2(filters)              -> (n, n) d^2, for duplicates


# ===================================================================================


class CvKalmanFilter:
    """
    Constant-velocity Kalman filter on [r, v] with DWNA process noise.
    """

    GATE = GATE_2DOF

    @staticmethod
    def z_of(a_det):
        return np.array([a_det["r"], a_det["v"]], dtype=float)

    @property
    def state(self):
        return self.x

    @property
    def cov(self):
        return self.P

    def __init__(self, a_cfg: RadarConfig):
        # Process noise: std of random accel [m/s^2]
        self.sa = a_cfg.TRACK_STD_A
        # Range-Doppler coupling [s], circular velocity span, fixed per config
        self.k = a_cfg.CHIRP_FC_HZ * a_cfg.T_EFF / a_cfg.CHIRP_BW_HZ
        self.vmax = a_cfg.MAX_VELOCITY

        # State [r, v] and covariance, set by init()
        self.x = None
        self.P = None

        self.R = np.diag([a_cfg.TRACK_STD_R**2, a_cfg.TRACK_STD_V**2])

        self.nis = 0.0

        # One H per detection kind, read-only
        self._Hs = {
            kind: np.array([[1.0, cpl], [0.0, 1.0]])
            for kind, cpl in (("up", self.k), ("down", -self.k), ("other", 0.0))
        }

    def _H(self, a_kind):
        """
        Measurement matrix: "up" / "down" carry range-Doppler coupling +-k*v
        """
        return self._Hs.get(a_kind, self._Hs["other"])

    def init(self, a_det):
        """
        Start track from one detection: invert H, no k*v range bias
        """
        Hi = np.linalg.inv(self._H(a_det["kind"]))  # removes coupling
        self.x = Hi @ self.z_of(a_det)
        self.P = Hi @ self.R @ Hi.T

    def predict(self, dt):
        """
        Predict state and covariance dt s ahead. dt may change per call
        """
        type(self).predict_batch([self], dt)

    def innovation(self, a_det):
        """
        Innovation y and covariance S for a detection
        """
        z = self.z_of(a_det)
        H = self._H(a_det["kind"])
        # Innovation, v wrapped (circular Doppler axis)
        y = z - H @ self.x
        y[1] = (y[1] + self.vmax) % (2 * self.vmax) - self.vmax
        # Residual covariance
        S = H @ self.P @ H.T + self.R
        return y, S

    def update(self, a_det):
        """
        Update state with a detection.
        """
        type(self).update_batch([self], [a_det])

    @classmethod
    def predict_batch(cls, a_filters, a_dt):
        """
        predict(dt) for every filter (batched)
        """
        f0 = a_filters[0]
        F = np.array([[1.0, a_dt], [0.0, 1.0]])
        # DWNA: accel a held over dt moves state by G*a -> Q = sa^2 G G'
        G = np.array([a_dt**2 / 2, a_dt])
        Q = f0.sa**2 * np.outer(G, G)
        x = np.array([f.x for f in a_filters])  # (n, 2)
        P = np.array([f.P for f in a_filters])  # (n, 2, 2)
        x = x @ F.T
        P = F @ P @ F.T + Q
        for f, xi, Pi in zip(a_filters, x, P):
            f.x, f.P = xi, Pi

    @classmethod
    def update_batch(cls, a_filters, a_dets):
        """
        update(det) for one (filter, detection) pair per index

        Returns (v, r, nis) arrays per pair:
        - v unrolled to track (predicted v + wrapped innovation)
        - r with Doppler coupling removed
        - NIS

        """
        f0 = a_filters[0]
        x = np.array([f.x for f in a_filters])  # (n, 2)
        P = np.array([f.P for f in a_filters])  # (n, 2, 2)
        z = np.array([[d["r"], d["v"]] for d in a_dets])
        coupling = {"up": f0.k, "down": -f0.k}
        c = np.array([coupling.get(d["kind"], 0.0) for d in a_dets])

        # Innovation y = z - H x, v wrapped
        y_r = z[:, 0] - (x[:, 0] + c * x[:, 1])
        y_v = (z[:, 1] - x[:, 1] + f0.vmax) % (2 * f0.vmax) - f0.vmax
        y = np.stack([y_r, y_v], axis=1)

        # Covariance
        # S = H P H' + R and closed-form inverse
        a, b, d = P[:, 0, 0], P[:, 0, 1], P[:, 1, 1]
        s_rr = a + 2 * c * b + c**2 * d + f0.R[0, 0]
        s_rv = b + c * d
        s_vv = d + f0.R[1, 1]
        det = s_rr * s_vv - s_rv**2
        Si = np.empty_like(P)
        Si[:, 0, 0] = s_vv / det
        Si[:, 0, 1] = Si[:, 1, 0] = -s_rv / det
        Si[:, 1, 1] = s_rr / det

        # Kalman gain K = P H' S^-1
        H = np.zeros_like(P)
        H[:, 0, 0] = H[:, 1, 1] = 1.0
        H[:, 0, 1] = c
        K = P @ H.transpose(0, 2, 1) @ Si
        I_KH = np.eye(2) - K @ H
        x_new = x + (K @ y[:, :, None])[:, :, 0]
        # Joseph form keeps P symmetric and positive
        P_new = I_KH @ P @ I_KH.transpose(0, 2, 1) + K @ f0.R @ K.transpose(0, 2, 1)

        # Calc NIS
        nis = np.einsum("ni,nij,nj->n", y, Si, y)

        for f, xi, Pi, ni in zip(a_filters, x_new, P_new, nis.tolist()):
            f.x, f.P, f.nis = xi, Pi, ni
        v = x[:, 1] + y_v
        return v, z[:, 0] - c * v, nis

    @classmethod
    def pair_d2(cls, a_filters):
        """
        Squared Mahalanobis distance between predicted measurements of every filter
        pair, (n, n): y = x_a - x_b (v wrapped), S = S_a + S_b, S = P + R
        """
        f0 = a_filters[0]
        x = np.array([f.x for f in a_filters])  # (n, 2)
        P = np.array([f.P for f in a_filters])  # (n, 2, 2)
        dr = x[:, None, 0] - x[None, :, 0]
        dv = x[:, None, 1] - x[None, :, 1]
        dv = (dv + f0.vmax) % (2 * f0.vmax) - f0.vmax
        s_rr = P[:, None, 0, 0] + P[None, :, 0, 0] + 2 * f0.R[0, 0]
        s_rv = P[:, None, 0, 1] + P[None, :, 0, 1]
        s_vv = P[:, None, 1, 1] + P[None, :, 1, 1] + 2 * f0.R[1, 1]
        det = s_rr * s_vv - s_rv**2
        return (s_vv * dr**2 - 2 * s_rv * dr * dv + s_rr * dv**2) / det

    @classmethod
    def d2_matrix(cls, a_filters, a_dets):
        """
        Squared Mahalanobis distance of every detection to every filter, (n_f, n_d).
        Same as d2(*f.innovation(det)), vectorized: H = [[1, c], [0, 1]], c = +k / -k / 0
        per detection kind -> S = H P H' + R closed-form 2x2.
        Filters share one config (k, vmax, R from first).
        """
        f0 = a_filters[0]
        x = np.array([f.x for f in a_filters])  # (n_f, 2)
        P = np.array([f.P for f in a_filters])  # (n_f, 2, 2)
        z = np.array([[d["r"], d["v"]] for d in a_dets])  # (n_d, 2)
        coupling = {"up": f0.k, "down": -f0.k}
        c = np.array([coupling.get(d["kind"], 0.0) for d in a_dets])[None, :]

        # Innovation y = z - H x, v wrapped
        x_r, x_v = x[:, 0:1], x[:, 1:2]
        y_r = z[None, :, 0] - (x_r + c * x_v)
        y_v = z[None, :, 1] - x_v
        y_v = (y_v + f0.vmax) % (2 * f0.vmax) - f0.vmax

        # S = H P H' + R, symmetric 2x2 [[s_rr, s_rv], [s_rv, s_vv]]
        a, b, d = P[:, 0, 0:1], P[:, 0, 1:2], P[:, 1, 1:2]
        s_rr = a + 2 * c * b + c**2 * d + f0.R[0, 0]
        s_rv = b + c * d
        s_vv = d + f0.R[1, 1]

        # y' S^-1 y, closed-form 2x2 inverse
        det = s_rr * s_vv - s_rv**2
        return (s_vv * y_r**2 - 2 * s_rv * y_r * y_v + s_rr * y_v**2) / det

    def rv(self):
        """
        Range, velocity accessor
        """
        return self.x[0], self.x[1]

    @classmethod
    def rv_batch(cls, a_filters):
        """
        Range, velocity accessor of every filter
        """
        x = np.array([f.x for f in a_filters])  # (n, 2)
        return x[:, 0], x[:, 1]

    def stop_if_slow(self, a_vbin):
        """
        Zero velocity inside MTI notch (stopped target doesnt drift)
        """
        self.x[1] = 0.0


# ===================================================================================


class CvExtendedKalmanFilter:
    """
    Constant-velocity Extended Kalman filter on [x, y, vx, vy] with DWNA process noise.
    """

    GATE = GATE_3DOF

    def __init__(self, a_cfg: RadarConfig):
        # Process noise: std of random accel [m/s^2]
        self.sr = a_cfg.TRACK_STD_R
        self.sv = a_cfg.TRACK_STD_V
        self.sa = a_cfg.TRACK_STD_A_XY
        self.sth_min = a_cfg.TRACK_STD_THETA_MIN_DEG
        # Range-Doppler coupling [s], circular velocity span, fixed per config
        self.k = a_cfg.CHIRP_FC_HZ * a_cfg.T_EFF / a_cfg.CHIRP_BW_HZ
        self.vmax = a_cfg.MAX_VELOCITY
        # State [x, y, vx, vy] and covariance; set by init()
        self.x = np.zeros((4,))
        self.P = np.zeros((4, 4))

        self.nis = 0.0

    @staticmethod
    def z_of(a_det):
        """
        Measurement vector [r, theta, v_r] of detection
        """
        return np.array([a_det["r"], math.radians(a_det["theta"]), a_det["v"]])

    def init(self, a_det):
        """
        Start track from one detection.
        """
        r, theta, r_dot = self.z_of(a_det)
        R = self._R(a_det)
        # Subtract range coupling*v_r
        r -= self._c(a_det["kind"]) * r_dot
        sin_th, cos_th = np.sin(theta), np.cos(theta)
        self.x = np.array(
            [
                r * sin_th,
                r * cos_th,
                r_dot * sin_th,
                r_dot * cos_th,
            ]
        )

        sr, sth, sv = np.sqrt(np.diag(R))

        # Columns: along line of sight (s, c), across (c, -s)
        Rot = np.array([[sin_th, cos_th], [cos_th, -sin_th]])
        # Diagonal in (along, across) frame, rotated into (x, y)
        P_pos = Rot @ np.diag([sr**2, (r * sth) ** 2]) @ Rot.T
        P_vel = Rot @ np.diag([sv**2, SV_ACROSS**2]) @ Rot.T

        self.P[:2, :2] = P_pos
        self.P[2:, 2:] = P_vel

    def predict(self, dt):
        # State transition matrix for constant velocity
        F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])
        self.x = F @ self.x

        # Process noise covariance
        Q = self.process_noise(dt)
        self.P = F @ self.P @ F.T + Q

    def process_noise(self, dt):
        """
        Process noise covariance, constant-velocity model with random accel
        """
        G = np.array(
            [
                [dt**4 / 4, 0, dt**3 / 2, 0],
                [0, dt**4 / 4, 0, dt**3 / 2],
                [dt**3 / 2, 0, dt**2, 0],
                [0, dt**3 / 2, 0, dt**2],
            ]
        )
        return self.sa**2 * G

    def jacobian_h(self, x, c):
        """
        Jacobian of radar measurement function h(x) at state x.
        Includes coupling coefficient
        From TI's guide: "Tracking radar targets with multiple reflection points"
        """
        x_, y, vx, vy = x
        r = safe_range(x_, y)
        r2 = r * r

        H = np.zeros((3, 4))
        H[0, 0] = x_ / r
        H[0, 1] = y / r
        H[1, 0] = y / r2
        H[1, 1] = -x_ / r2
        H[2, 0] = y * (vx * y - vy * x_) / (r2 * r)
        H[2, 1] = x_ * (vy * x_ - vx * y) / (r2 * r)
        H[2, 2] = x_ / r
        H[2, 3] = y / r

        # Add range coupling
        H[0] += c * H[2]

        return H

    def h(self, x, c):
        """
        Radar measurement function, includes coupling coefficient
        [x, y, vx, vy] -> [r, theta, r_dot]
        """
        x_ = x[..., 0]
        y = x[..., 1]
        vx = x[..., 2]
        vy = x[..., 3]
        r = safe_range(x_, y)
        r_dot = (x_ * vx + y * vy) / r
        theta = np.arctan2(x_, y)
        return np.stack([r + c * r_dot, theta, r_dot], axis=-1)

    def innovation(self, a_det):
        """
        Innovation y and covariance S for a detection
        """
        return self._innovation(a_det)[:2]

    def _innovation(self, a_det):
        """
        Innovation y, covariance S, plus H and R built for them (update reuses)
        """
        z = self.z_of(a_det)
        R = self._R(a_det)
        coupling = self._c(a_det["kind"])
        # Measurement innovation
        y = z - self.h(self.x, coupling)
        # Normalize angle to [-pi, pi]
        y[1] = (y[1] + np.pi) % (2 * np.pi) - np.pi
        # Wrap velocity
        y[2] = (y[2] + self.vmax) % (2 * self.vmax) - self.vmax
        # Residual covariance
        H = self.jacobian_h(self.x, coupling)
        S = H @ self.P @ H.T + R
        return y, S, H, R

    def update(self, a_det):
        """
        Update state with one detection. NIS in self.nis
        """
        y, S, H, R = self._innovation(a_det)
        # Kalman gain K = P H' S^-1, solved instead of inverted (S, P symmetric: K' = S^-1 H P)
        K = np.linalg.solve(S, H @ self.P).T
        # A-posteriori state estimate
        self.x += K @ y
        # A-posteriori covariance
        # Joseph form keeps symmetry and positive under rounding
        I_KH = np.eye(4) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T
        self.nis = d2(y, S)

    def _R(self, a_det):
        """
        Per-detection R
        """
        snr_db = a_det.get("snr_db")
        if snr_db is None or not math.isfinite(snr_db):
            snr_db = SNR_NOMINAL_DB  # old recordings carry no snr_db
        snr = 10.0 ** (snr_db / 10.0)
        cos_th = max(math.cos(math.radians(a_det["theta"])), 0.5)
        s_th = 1.0 / (math.pi * cos_th * math.sqrt(snr))
        if a_det["kind"] == "both":
            s_th /= math.sqrt(2)
        s_th = max(s_th, math.radians(self.sth_min))  # floor
        return np.diag([self.sr**2, s_th**2, self.sv**2])

    def _c(self, a_kind):
        """
        Returns coupling constant
        """
        return {"up": self.k, "down": -self.k}.get(a_kind, 0.0)

    def rv(self):
        """
        Returns range, velocity
        """
        x, y, vx, vy = self.x
        r = safe_range(x, y)
        return r, (x * vx + y * vy) / r

    @classmethod
    def rv_batch(cls, a_filters):
        """
        Returns range, velocity of every filter
        """
        x = np.array([f.x for f in a_filters])  # (n, 4)
        r = safe_range(x[:, 0], x[:, 1])
        return r, (x[:, 0] * x[:, 2] + x[:, 1] * x[:, 3]) / r


# ===================================================================================
@dataclass
class Track:  # data only, no logic
    id: int
    filter: CvKalmanFilter | CvExtendedKalmanFilter
    status: str  # TENTATIVE / CONFIRMED
    hits: deque  # last N CPIs as 0/1; M-of-N = sum(hits) >= M
    misses: int  # consecutive misses; tentative dies at 2
    birth_t: float  # birth time
    last_t: float  # last update time, for delete-by-time
    age: int  # CPIs since birth
    snr_db: float  # from the last detection
    nis: float | None  # this CPI's update; None when born or coasting
    kin_ok: bool  # kinematic check passed
    met: bool  # M of N reached once (sticky, hits window slides on)
    history: deque  # (t, r, v), trimmed to last TRACK_KINEMATIC_WINDOW_S
    # Running sums over history for kinematic check: [n, t, t^2, r, t*r, v],
    # t relative to birth_t (cancellation harmless). See _hist_add / _hist_drop
    sums: list
    num: int | None = None  # display number, set at confirmation, never reused
    theta: float | None = None  # deg, last matched detection; None without TDM

    def snapshot(self) -> dict:
        """Plain copy for signals, worker keeps mutating live Track"""
        r, v = self.filter.rv()
        return {
            "id": self.id,
            "status": self.status,
            "r": float(r),
            "v": float(v),
            "snr_db": float(self.snr_db),
            "age": self.age,
            "nis": None if self.nis is None else float(self.nis),
            "kin_ok": bool(self.kin_ok),
            "coasting": self.misses > 0,  # no detection this CPI
            "num": self.num,
            "theta": None if self.theta is None else float(self.theta),
            "birth_t": float(self.birth_t),
        }


# ===================================================================================
def _hist_add(a_trk, a_t, a_r, a_v):
    """
    Append (t, r, v) to history and running sums. Drop oldest if deque full
    """
    h = a_trk.history
    if len(h) == h.maxlen:
        _hist_drop(a_trk)
    h.append((a_t, a_r, a_v))
    t = a_t - a_trk.birth_t
    s = a_trk.sums
    s[0] += 1
    s[1] += t
    s[2] += t * t
    s[3] += a_r
    s[4] += t * a_r
    s[5] += a_v


def _hist_drop(a_trk):
    """
    Pop oldest history entry, remove from running sums
    """
    t, r, v = a_trk.history.popleft()
    t -= a_trk.birth_t
    s = a_trk.sums
    s[0] -= 1
    s[1] -= t
    s[2] -= t * t
    s[3] -= r
    s[4] -= t * r
    s[5] -= v


def kinematic(a_sums, a_cfg, a_age_s) -> bool | None:
    """
    Does range move with measured velocity? i.e. object follows Newtonian physics

    Fits slope=r/t of history (last TRACK_KINEMATIC_WINDOW_S as running sums
    [n, t, t^2, r, t*r, v]), compares with mean measured v

    a_age_s = track age in seconds
    """
    n, s_t, s_t2, s_r, s_tr, s_v = a_sums
    if n < 2:
        return None
    k = a_cfg.TRACK_KINEMATIC_SIGMA
    v_mean = s_v / n

    # LS slope of r over t, its std for white range noise
    s_tt = s_t2 - s_t * s_t / n
    if s_tt <= 0.0:
        return None
    slope = (s_tr - s_t * s_r / n) / s_tt
    sigma_slope = a_cfg.TRACK_STD_R / math.sqrt(s_tt)
    sigma_vmean = a_cfg.TRACK_STD_V / math.sqrt(n)

    sigma = math.hypot(sigma_slope, sigma_vmean)
    miss = abs(slope - v_mean)
    # Fail on strong disagreement. |v_mean| / 2 floor: coloured range noise and
    # manoeuvres dont fail real track, spur misses by all of |v| (guide 6.5)
    if miss > max(2 * k * sigma, 0.5 * abs(v_mean)):
        return False
    # Pass only once slope precise enough to tell from zero with margin,
    # else spur (slope 0) passes by noise ~half the time at boundary
    if sigma < abs(v_mean) / (KIN_MARGIN * k) and miss < k * sigma:
        return True
    # Undecided after window: too slow to tell from stationary -> pass
    return True if a_age_s >= a_cfg.TRACK_KINEMATIC_WINDOW_S else None


# ===================================================================================
class Tracker:
    def __init__(self, a_cfg: RadarConfig):
        # tracks=[], next_id=0, t=None, pair_n={}, vbin/rbin/gate computed once
        self.tracks = []
        self.next_id = 0
        self.next_num = 0  # confirmation counter (Track.num)
        self.t = None
        self.pair_n = {}
        self.cfg = a_cfg
        self._cap_warned = False
        # Velocity bin, MTI notch one bin wide
        t_rep = a_cfg.LEGS_PER_PERIOD * a_cfg.T_EFF
        self.vbin = (c / a_cfg.CHIRP_FC_HZ) / (2 * t_rep * a_cfg.CHIRP_REPS)
        self.rbin = c / (2 * a_cfg.CHIRP_BW_HZ)

        # pick filter class
        self.filter_cls = CvKalmanFilter
        self.gate = self.filter_cls.GATE

    def update(self, a_dets, a_t, a_mti_en) -> list[Track]:
        """
        Update all tracks.
        a. Predict all.
        b. Stage 1: confirmed vs all detections.
        c. Stage 2: tentative vs leftover detections.
        d. Apply matched updates.
        e. Count miss for every unmatched track.
        f. _birth from still unmatched.
        g. _manage, then _merge.
        h. Return tracks.
        """
        # 1. delta time
        if self.t is not None:
            dt = a_t - self.t
        else:
            dt = 0.0
        self.t = a_t
        # 2. Predict
        if self.tracks:
            type(self.tracks[0].filter).predict_batch(
                [t.filter for t in self.tracks], dt
            )
        conf = [t for t in self.tracks if t.status == CONFIRMED]
        tent = [t for t in self.tracks if t.status == TENTATIVE]
        # Stage 1: confirmed vs detections
        pairs1, left1 = self._assign(a_tracks=conf, a_dets=a_dets)
        # Stage 2: tentative vs leftovers
        rest = [a_dets[j] for j in left1]
        pairs2, left2 = self._assign(a_tracks=tent, a_dets=rest)
        # Collect results
        matches = [(conf[i], a_dets[j]) for i, j in pairs1]
        matches += [(tent[i], a_dets[left1[j]]) for i, j in pairs2]

        # 3. Matched tracks update, else incr miss
        if matches:
            self._apply_all(matches, a_t)
        matched = {id(trk) for trk, _ in matches}
        for trk in conf + tent:
            if id(trk) not in matched:
                trk.hits.append(0)
                trk.misses += 1
                trk.age += 1
                trk.nis = None

        # 4. Birth from detections no track claimed
        # NOTE: det inside confirmed track's NMS window = second dot of same target
        # Same for tentative track that met M of N, awaiting kinematic check: else
        # spur's neighbours keep birthing tracks around it
        free = np.asarray(left1, dtype=int)[left2]
        guard = conf + [t for t in tent if t.met]
        if guard and len(free):
            free = free[~self._in_window(guard, [a_dets[j] for j in free]).any(axis=0)]
        births = [a_dets[j] for j in free]
        self._birth(births, a_t)

        # 5. Delete, confirm, merge
        self._manage(a_t, a_mti_en)
        self._merge()
        return self.tracks

    def _apply_all(self, a_matches, a_t):
        """
        Batched filter update and bookkeeping.
        History holds v unrolled to track (predicted v + wrapped innovation),
        range with coupling removed
        """
        filters = [trk.filter for trk, _ in a_matches]
        dets = [det for _, det in a_matches]
        vs, rs, nis = type(filters[0]).update_batch(filters, dets)
        for (trk, det), v, r, ni in zip(
            a_matches, vs.tolist(), rs.tolist(), nis.tolist()
        ):
            trk.hits.append(1)
            trk.misses = 0
            trk.age += 1
            trk.last_t = a_t
            trk.snr_db = det.get("snr_db", np.nan)
            trk.nis = ni
            trk.theta = det.get("theta")
            _hist_add(trk, a_t, r, v)
            while trk.history[0][0] < a_t - self.cfg.TRACK_KINEMATIC_WINDOW_S:
                _hist_drop(trk)

    def _in_window(self, a_tracks, a_dets):
        """
        (n_tracks, n_dets) bool:
        Detection within +-NMS_SIZE R and V bins of track's predicted measurement
        (v wrapped)
        """
        f0 = a_tracks[0].filter

        r_p, v_p = type(f0).rv_batch(a_filters=[t.filter for t in a_tracks])
        r_p, v_p = r_p[:, None], v_p[:, None]  # (n_tracks, 1)
        coupling = {"up": f0.k, "down": -f0.k}
        cc = np.array([coupling.get(d["kind"], 0.0) for d in a_dets])[None, :]
        r_det = np.array([d["r"] for d in a_dets])[None, :]
        v_det = np.array([d["v"] for d in a_dets])[None, :]
        dr = r_det - (r_p + cc * v_p)
        dv = (v_det - v_p + f0.vmax) % (2 * f0.vmax) - f0.vmax
        return (np.abs(dr) <= NMS_SIZE * self.rbin) & (
            np.abs(dv) <= NMS_SIZE * self.vbin
        )

    def _assign(self, a_tracks, a_dets):
        """
        One GNN stage: tracks to detections on d^2, in-gate only.

        Returns (pairs, leftover):
            - pairs = [(track_i, det_j)]
            - leftover = indices of unmatched detections
        """
        n_t, n_d = len(a_tracks), len(a_dets)
        if n_t == 0 or n_d == 0:
            return [], list(range(n_d))
        filters = [trk.filter for trk in a_tracks]
        D = type(filters[0]).d2_matrix(filters, a_dets)

        cost = np.where(D < self.gate, D, OUT_OF_GATE)
        rows, cols = linear_sum_assignment(cost)
        # Assign only in-gate pairs
        ok = D[rows, cols] < self.gate
        rows, cols = rows[ok], cols[ok]
        pairs = list(zip(rows.tolist(), cols.tolist()))
        leftover = np.setdiff1d(np.arange(n_d), cols).tolist()
        return pairs, leftover

    def _birth(self, a_dets, a_t):
        """
        Start tentative track from each detection
        Skip if TRACK_MIN_SPEED or TRACK_MAX apply
        """
        for det in a_dets:
            if abs(det["v"]) < self.cfg.TRACK_MIN_SPEED:
                continue
            if len(self.tracks) >= self.cfg.TRACK_MAX:
                if not self._cap_warned:
                    print(
                        f"Tracker: at TRACK_MAX ({self.cfg.TRACK_MAX}), no new tracks"
                    )
                    self._cap_warned = True
                break

            # Pick filter type
            filt = self.filter_cls(self.cfg)
            filt.init(det)

            self.tracks.append(
                Track(
                    id=self.next_id,
                    filter=filt,
                    status=TENTATIVE,
                    hits=deque([1], maxlen=self.cfg.TRACK_CONFIRM_N),
                    misses=0,
                    birth_t=a_t,
                    last_t=a_t,
                    age=1,
                    snr_db=det.get("snr_db", np.nan),
                    nis=None,
                    kin_ok=False,
                    met=False,
                    history=deque(maxlen=HISTORY_CAP),
                    sums=[0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    theta=det.get("theta"),
                )
            )
            r, v = filt.rv()
            _hist_add(self.tracks[-1], a_t, r, v)
            self.next_id += 1

    def _manage(self, a_t, a_mti_en):
        """
        Kinematic check, confirm (M of N + check passed), delete (by time).
        """
        cfg = self.cfg
        keep = []
        for trk in self.tracks:
            kin = kinematic(trk.sums, cfg, a_t - trk.birth_t)
            if kin is False:
                continue  # fails the check: deleted
            trk.kin_ok = kin is True
            trk.met = trk.met or sum(trk.hits) >= cfg.TRACK_CONFIRM_M

            if (
                trk.status == TENTATIVE
                and trk.kin_ok
                and trk.hits[-1]
                and sum(trk.hits) >= cfg.TRACK_CONFIRM_M
            ):
                trk.status = CONFIRMED
                trk.num = self.next_num
                self.next_num += 1

            if trk.status == CONFIRMED or trk.met:
                # Confirmed, or tentative awaiting check: by time
                # Confirmed track inside MTI notch (person who stopped) has
                # no detections by design -> coasts longer
                limit = cfg.TRACK_DELETE_S
                _, v = trk.filter.rv()
                if a_mti_en and trk.status == CONFIRMED and abs(v) < self.vbin:
                    limit = cfg.TRACK_STOP_COAST_S
                    # stopped target must not drift
                    if trk.last_t != a_t:
                        trk.filter.stop_if_slow(self.vbin)

                dead = a_t - trk.last_t > limit
            else:
                dead = trk.misses >= 2
            if not dead:
                keep.append(trk)
        self.tracks = keep

    def _merge(self):
        """
        Two confirmed tracks in each other's gate TRACK_MERGE_N CPIs in a row
        = one target. Delete younger (higher ID)
        pair_n counts streaks
        """
        conf = [t for t in self.tracks if t.status == CONFIRMED]
        if len(conf) < 2:
            self.pair_n = {}
            return
        D = type(conf[0].filter).pair_d2([t.filter for t in conf])
        ii, jj = np.nonzero(np.triu(D < self.gate, 1))

        pair_n, doomed = {}, set()
        for i, j in zip(ii.tolist(), jj.tolist()):
            key = tuple(sorted((conf[i].id, conf[j].id)))
            pair_n[key] = self.pair_n.get(key, 0) + 1
            if pair_n[key] >= self.cfg.TRACK_MERGE_N:
                doomed.add(key[1])
        # Pair not close this CPI starts over (not in pair_n)
        self.pair_n = {k: n for k, n in pair_n.items() if not doomed.intersection(k)}
        if doomed:
            self.tracks = [t for t in self.tracks if t.id not in doomed]


# ===================================================================================
def d2(y, S):
    """
    Squared Mahalanobis distance of innovation y
    """
    return y @ np.linalg.solve(S, y)


# ===================================================================================


if __name__ == "__main__":
    print("Hello world! :-D")
