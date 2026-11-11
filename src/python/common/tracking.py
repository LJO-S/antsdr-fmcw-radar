"""
All code related to radar's tracker internals.
"""

import numpy as np
from collections import deque
from dataclasses import dataclass
from scipy.optimize import linear_sum_assignment

from common.config import c

GATE_RV = 9.21  # chi-square, 2 dof, 99 %
OUT_OF_GATE = 1e9  # assignment cost of a pair outside the gate
CONFIRMED = "confirmed"  # Track.status values
TENTATIVE = "tentative"
HISTORY_CAP = 500  # safety cap on the history deque; it is trimmed by time
KIN_WINDOW_S = 3.0  # the kinematic check looks at this much history
KIN_MARGIN = 2.0  # pass needs sigma_slope < |v| / (KIN_MARGIN * k)

# Filter interface (one instance per track), so a KF, an EKF or an IMM plug in without
# touching association or track management. Detections are target dicts from process_cpi.
#
#   init(det)           start from one detection
#   predict(dt)         dt seconds ahead
#   innovation(det)     -> (y, S), for gating
#   update(det)         fold the detection in
#   state, cov          for the tracker, the GUI and the tests


# ===================================================================================


class CvKalmanFilter:
    """
    Constant-velocity Kalman filter on [r, v] with DWNA process noise.
    """

    @staticmethod
    def z_of(a_det):
        return np.array([a_det["r"], a_det["v"]], dtype=float)

    @property
    def state(self):
        return self.x

    @property
    def cov(self):
        return self.P

    def __init__(self, a_cfg):
        # Process noise: std of the random acceleration [m/s^2]
        self.sa = a_cfg.TRACK_STD_A
        # Range-Doppler coupling [s] and the circular velocity span, fixed per config
        self.k = a_cfg.CHIRP_FC_HZ * a_cfg.T_EFF / a_cfg.CHIRP_BW_HZ
        self.vmax = a_cfg.MAX_VELOCITY

        # State vector [r, v] and its covariance; set by init()
        self.x = None
        self.P = None

        self.R = np.diag([a_cfg.TRACK_STD_R**2, a_cfg.TRACK_STD_V**2])

        self.nis = 0.0

    def _H(self, a_kind):
        """
        Measurement matrix: "up" / "down" carry the range-Doppler coupling +-k*v
        """
        coupling = {"up": self.k, "down": -self.k}.get(a_kind, 0.0)
        return np.array([[1.0, coupling], [0.0, 1.0]])

    def init(self, a_det):
        """
        A track starts from one detection: invert H so the range has no k*v bias.
        """
        Hi = np.linalg.inv(self._H(a_det["kind"]))  # invert to get removal of coupling
        self.x = Hi @ self.z_of(a_det)
        self.P = Hi @ self.R @ Hi.T

    def predict(self, dt):
        """
        Predict the state and covariance dt seconds ahead (dt may change every call)
        """
        # State transition matrix
        F = np.array([[1.0, dt], [0.0, 1.0]])
        # DWNA: acceleration 'a' held over 'dt' moves the state by G*a, so Q = sa^2 G G'
        G = np.array([dt**2 / 2, dt])
        Q = self.sa**2 * np.outer(G, G)
        # Predict the state and its covariance
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def innovation(self, a_det):
        """
        Innovation y and its covariance S for a detection
        """
        z = self.z_of(a_det)
        H = self._H(a_det["kind"])
        # Measurement innovation (the Doppler axis is circular so wrap v)
        y = z - H @ self.x
        y[1] = (y[1] + self.vmax) % (2 * self.vmax) - self.vmax
        # Residual covariance
        S = H @ self.P @ H.T + self.R
        return y, S

    def update(self, a_det):
        """
        Update the state with a detection
        """
        H = self._H(a_det["kind"])
        y, S = self.innovation(a_det)
        # Kalman gain K = P H' S^-1, solved instead of inverted (S, P symmetric: K' = S^-1 H P)
        K = np.linalg.solve(S, H @ self.P).T
        # A-posteriori state estimate
        self.x = self.x + K @ y
        # A-posteriori covariance
        # Use Joseph form to force symmetry and positive under rounding
        I_KH = np.eye(2) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ self.R @ K.T
        self.nis = d2(y, S)

    @classmethod
    def pair_d2(cls, a_filters):
        """
        Squared Mahalanobis distance between the predicted measurements of every pair
        of filters, (n, n): y = x_a - x_b (v wrapped), S = S_a + S_b with S = P + R.
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
        Same numbers as d2(*f.innovation(det)), vectorized: H = [[1, c], [0, 1]] has
        c = +k / -k / 0 per detection kind, so S = H P H' + R is closed-form 2x2.
        All filters share one config (k, vmax, R are read from the first).
        """
        f0 = a_filters[0]
        x = np.array([f.x for f in a_filters])  # (n_f, 2)
        P = np.array([f.P for f in a_filters])  # (n_f, 2, 2)
        z = np.array([[d["r"], d["v"]] for d in a_dets])  # (n_d, 2)
        coupling = {"up": f0.k, "down": -f0.k}
        c = np.array([coupling.get(d["kind"], 0.0) for d in a_dets])[None, :]

        # Innovation y = z - H x, v wrapped (the Doppler axis is circular)
        x_r, x_v = x[:, 0:1], x[:, 1:2]
        y_r = z[None, :, 0] - (x_r + c * x_v)
        y_v = z[None, :, 1] - x_v
        y_v = (y_v + f0.vmax) % (2 * f0.vmax) - f0.vmax

        # S = H P H' + R, symmetric 2x2 [[s_rr, s_rv], [s_rv, s_vv]]
        a, b, d = P[:, 0, 0:1], P[:, 0, 1:2], P[:, 1, 1:2]
        s_rr = a + 2 * c * b + c**2 * d + f0.R[0, 0]
        s_rv = b + c * d
        s_vv = d + f0.R[1, 1]

        # y' S^-1 y with the closed-form 2x2 inverse
        det = s_rr * s_vv - s_rv**2
        return (s_vv * y_r**2 - 2 * s_rv * y_r * y_v + s_rr * y_v**2) / det


# ===================================================================================

# class CvExtendedKalmanFilter:
#     """
#     Constant-velocity Extended Kalman filter on [x, y, vx, vy] with DWNA process noise.
#     """

#     def __init__(self, sa):
#         # Process noise: std of the random acceleration [m/s^2]
#         self.sa = sa
#         # State vector [r, v] and its covariance; set by init()
#         self.x = np.zeros((4, 1))
#         self.P = np.zeros((4, 4))

#     def init(self, z, R):
#         r, theta, r_dot = z
#         s, c = np.sin(theta), np.cos(theta)
#         # State vector [x, y, vx, vy] and its covariance from one detection
#         self.x = np.array(
#             [
#                 r * s,
#                 r * c,
#                 r_dot * s,
#                 r_dot * c,
#             ]
#         )

#         sr, sth, sv = np.sqrt(np.diag(R))

#         # Columns: along the line of sight (s, c) and across it (c, -s)
#         Rot = np.array([[s, c], [c, -s]])
#         # Diagonal in the (along, across) frame, rotated into (x, y)
#         P_pos = Rot @ np.diag([sr**2, (r * sth) ** 2]) @ Rot.T
#         P_vel = (
#             Rot @ np.diag([sv**2, SV_ACROSS**2]) @ Rot.T
#         )  # TODO where should this be set?

#         self.P[:2, :2] = P_pos
#         self.P[2:, 2:] = P_vel

#         # Measurement noise covariance: (r, theta, r_dot)
#         self.R = R
#         self.dim_z = 3

#     def predict(self, dt):
#         # State transition matrix for constant velocity
#         F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])
#         self.x = F @ self.x

#         # Process noise covariance
#         Q = self.process_noise(dt)
#         self.P = F @ self.P @ F.T + Q

#     def process_noise(self, dt):
#         """
#         Process noise covariance for constant-velocity model with random acceleration.
#         """
#         G = np.array(
#             [
#                 [dt**4 / 4, 0, dt**3 / 2, 0],
#                 [0, dt**4 / 4, 0, dt**3 / 2],
#                 [dt**3 / 2, 0, dt**2, 0],
#                 [0, dt**3 / 2, 0, dt**2],
#             ]
#         )
#         return self.sa**2 * G

#     def jacobian_h(self, x):
#         """
#         Jacobian of the radar measurement function h(x) at state x.
#         From TI's guide: "Tracking radar targets with multiple reflection points"
#         """
#         x_, y, vx, vy = x
#         r2 = x_**2 + y**2
#         r = np.sqrt(r2)

#         # Avoid division by zero
#         if r < 1e-6:
#             # TODO should probably not raise a ValueError here during runtime....
#             raise ValueError("Division by zero in jacobian_h")

#         H = np.zeros((3, 4))
#         H[0, 0] = x_ / r
#         H[0, 1] = y / r
#         H[1, 0] = y / r2
#         H[1, 1] = -x_ / r2
#         H[2, 0] = y * (vx * y - vy * x_) / (r2 * r)
#         H[2, 1] = x_ * (vy * x_ - vx * y) / (r2 * r)
#         H[2, 2] = x_ / r
#         H[2, 3] = y / r

#         return H

#     def h(self, x):
#         """
#         Radar measurement function:
#         [x, y, vx, vy] -> [r, theta, r_dot]
#         """
#         x_ = x[..., 0]
#         y = x[..., 1]
#         vx = x[..., 2]
#         vy = x[..., 3]
#         r = np.sqrt(x_**2 + y**2)
#         r_dot = (x_ * vx + y * vy) / r
#         theta = np.arctan2(x_, y)
#         return np.stack([r, theta, r_dot], axis=-1)

#     def innovation(self, z, R):
#         """
#         Residual y and its covariance S for measurement z
#         """
#         z = np.asarray(z, dtype=float)[: self.dim_z]
#         # Measurement innovation
#         y = z - self.h(self.x)
#         # Normalize angle to [-pi, pi]
#         y[1] = (y[1] + np.pi) % (2 * np.pi) - np.pi
#         # Residual covariance
#         H = self.jacobian_h(self.x)
#         S = H @ self.P @ H.T + R
#         return y, S

#     def update(self, z, R):
#         """
#         Update the state and covariance with measurement z.
#         Returns the NIS value for gating.
#         """
#         y, S = self.innovation(z, R)
#         H = self.jacobian_h(self.x)
#         # Kalman gain K = P H' S^-1, solved instead of inverted (S, P symmetric: K' = S^-1 H P)
#         K = np.linalg.solve(S, H @ self.P).T
#         # A-posteriori state estimate
#         self.x += K @ y
#         # A-posteriori covariance
#         # Use Joseph form forcing symmetry and positive under rounding
#         I_KH = np.eye(4) - K @ H
#         self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T
#         return dsp.d2(y, S)

# Track:
#    - Fields:
#      - id
#      - status (tentative or confirmed)
#      - filter
#      - hits and misses
#      - last update time
#      - age
#      - last snr_db
#      - last NIS
#      - a ~3 s deque of (t, r, v)
#    - Unrolled v: the history stores v unrolled to the track, which is predicted v plus the wrapped innovation.


# ===================================================================================
@dataclass
class Track:  # data only, no logic
    id: int
    filter: CvKalmanFilter
    status: str  # TENTATIVE / CONFIRMED (the constants above)
    hits: deque  # last N CPIs as 0/1; M-of-N = sum(hits) >= M
    misses: int  # consecutive misses; a tentative track dies at 2
    birth_t: float  # time the track was born
    last_t: float  # time of last update, for delete-by-time
    age: int  # CPIs since birth
    snr_db: float  # from the last detection
    nis: float  # from the last update
    kin_ok: bool  # kinematic check has passed
    met: bool  # M of N has been reached once (sticky: the hits window slides on)
    history: deque  # (t, r, v), trimmed to the last KIN_WINDOW_S
    # TODO unrolled v?


# ===================================================================================
def kinematic(a_history, a_cfg, a_age_s) -> bool | None:
    """
    Does the range move with the measured velocity?
    Fits slope=r/t of history (the last KIN_WINDOW_S) and compares with mean measured v.
    a_age_s is the track's age in seconds.
    """
    hist = np.asarray(a_history, dtype=float)  # rows (t, r, v)
    n = len(hist)
    k = a_cfg.TRACK_KINEMATIC_SIGMA
    v_mean = hist[:, 2].mean()

    if n < 2:
        return None

    # Least-squares slope of 'r-over-t' and its std for white range noise
    t = hist[:, 0] - hist[:, 0].mean()
    s_tt = t @ t
    if s_tt == 0.0:
        return None
    slope = (t @ (hist[:, 1] - hist[:, 1].mean())) / s_tt
    sigma_slope = a_cfg.TRACK_STD_R / np.sqrt(s_tt)
    sigma_vmean = a_cfg.TRACK_STD_V / np.sqrt(n)

    sigma = np.hypot(sigma_slope, sigma_vmean)
    miss = abs(slope - v_mean)
    # Fail on strong disagreement
    if miss > 2 * k * sigma:
        return False
    # Pass only once the slope is precise enough to tell it from zero with margin
    # or a spur (slope 0) passes by noise about half the time at the boundary.
    if sigma < abs(v_mean) / (KIN_MARGIN * k) and miss < k * sigma:
        return True
    # Still undecided after KIN_WINDOW_S: too slow to tell from stationary so pass
    return True if a_age_s >= KIN_WINDOW_S else None


# ===================================================================================
class Tracker:
    def __init__(self, a_cfg):
        # tracks=[], next_id=0, t=None, pair_n={}, vbin/rbin/gate computed once
        self.tracks = []
        self.next_id = 0
        self.t = None
        self.pair_n = {}
        self.gate = GATE_RV
        self.cfg = a_cfg
        self._cap_warned = False
        # Velocity bin: the MTI notch is one bin wide
        t_rep = a_cfg.LEGS_PER_PERIOD * a_cfg.T_EFF
        self.vbin = (c / a_cfg.CHIRP_FC_HZ) / (2 * t_rep * a_cfg.CHIRP_REPS)

    def update(self, a_dets, a_t, a_mti_en) -> list[Track]:
        """
        Update all tracks.
        a. Predict all tracks.
        b. Stage 1: confirmed tracks against all detections.
        c. Stage 2: tentative tracks against the leftover detections.
        d. Apply the matched updates.
        e. Count a miss for every unmatched track.
        f. _birth from what's still unmatched.
        g. _manage, then _merge.
        h. Return the tracks.
        """
        # 1. delta time
        if self.t is not None:
            dt = a_t - self.t
        else:
            dt = a_t
        self.t = a_t
        # 2. Predict
        for trk in self.tracks:
            trk.filter.predict(dt)
        conf = [t for t in self.tracks if t.status == CONFIRMED]
        tent = [t for t in self.tracks if t.status == TENTATIVE]
        # Stage 1: confirmed tracks against detections
        pairs1, left1, D1 = self._assign(a_tracks=conf, a_dets=a_dets)
        # Stage 2: tentative tracks against leftovers
        rest = [a_dets[j] for j in left1]
        pairs2, left2, _ = self._assign(a_tracks=tent, a_dets=rest)
        # Collect results
        matches = [(conf[i], a_dets[j]) for i, j in pairs1]
        matches += [(tent[i], a_dets[left1[j]]) for i, j in pairs2]

        # 3. Matched tracks update. If not found then incr miss
        for trk, det in matches:
            # NOTE: if latency sky-rockets, vectorize here
            self._apply(trk, det, a_t)
        matched = {id(trk) for trk, _ in matches}
        for trk in conf + tent:
            if id(trk) not in matched:
                trk.hits.append(0)
                trk.misses += 1
                trk.age += 1

        # 4. Birth trackers from the detections no track claimed.
        # NOTE: One inside a confirmed track's gate is a second dot of same target
        free = np.asarray(left1, dtype=int)[left2]
        if conf:
            free = free[D1[:, free].min(axis=0) >= self.gate]
        births = [a_dets[j] for j in free]
        self._birth(births, a_t)

        # 5. Delete, confirm, merge
        self._manage(a_t, a_mti_en)
        self._merge()
        return self.tracks

    def _apply(self, a_trk, a_det, a_t):
        """
        Fold a matched detection into a track.
        """
        filt = a_trk.filter
        # Before update(): the innovation against the predicted state. The history
        # holds v unrolled to the track (predicted v + wrapped innovation) and the
        # range with the coupling removed.
        y, _ = filt.innovation(a_det)
        v = filt.x[1] + y[1]
        r = a_det["r"] - filt._H(a_det["kind"])[0, 1] * v
        filt.update(a_det)

        a_trk.hits.append(1)
        a_trk.misses = 0
        a_trk.age += 1
        a_trk.last_t = a_t
        a_trk.snr_db = a_det.get("snr_db", np.nan)
        a_trk.nis = filt.nis
        a_trk.history.append((a_t, r, v))
        while a_trk.history[0][0] < a_t - KIN_WINDOW_S:
            a_trk.history.popleft()

    def _assign(self, a_tracks, a_dets):
        """
        One GNN stage: tracks to detections on d^2, inside the gate only.

        Returns (pairs, leftover, D):
            - pairs = [(track_i, det_j)]
            - leftover = indices of unmatched detections,
            - D = the d^2 matrix (tracks x dets)
        """
        n_t, n_d = len(a_tracks), len(a_dets)
        if n_t == 0 or n_d == 0:
            return [], list(range(n_d)), np.empty((n_t, n_d))
        filters = [trk.filter for trk in a_tracks]
        D = type(filters[0]).d2_matrix(filters, a_dets)

        cost = np.where(D < self.gate, D, OUT_OF_GATE)
        rows, cols = linear_sum_assignment(cost)
        # Assign only in-gate pairs
        ok = D[rows, cols] < self.gate
        rows, cols = rows[ok], cols[ok]
        pairs = list(zip(rows.tolist(), cols.tolist()))
        leftover = np.setdiff1d(np.arange(n_d), cols).tolist()
        return pairs, leftover, D

    def _birth(self, a_dets, a_t):
        """
        Start a tentative track from each detection
        Skip if TRACK_MIN_SPEED or TRACK_MAX apply.
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
            filt = CvKalmanFilter(self.cfg)
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
                    nis=0.0,
                    kin_ok=False,
                    met=False,
                    history=deque([(a_t, filt.x[0], filt.x[1])], maxlen=HISTORY_CAP),
                )
            )
            self.next_id += 1

    def _manage(self, a_t, a_mti_en):
        """
        Kinematic check, confirm (M of N + check passed) and delete (by time).

        """
        cfg = self.cfg
        keep = []
        for trk in self.tracks:
            kin = kinematic(trk.history, cfg, a_t - trk.birth_t)
            if kin is False:
                continue  # fails the check: deleted
            trk.kin_ok = kin is True
            trk.met = trk.met or sum(trk.hits) >= cfg.TRACK_CONFIRM_M
            if trk.status == TENTATIVE and trk.met and trk.kin_ok:
                trk.status = CONFIRMED

            if trk.status == CONFIRMED or trk.met:
                # Confirmed, or tentative and only waiting for the check: by time.
                # A confirmed track inside the MTI notch (a person who stopped) has
                # no detections by design, so it coasts longer.
                limit = cfg.TRACK_DELETE_S
                if (
                    a_mti_en
                    and trk.status == CONFIRMED
                    and abs(trk.filter.x[1]) < self.vbin
                ):
                    limit = cfg.TRACK_STOP_COAST_S
                dead = a_t - trk.last_t > limit
            else:
                dead = trk.misses >= 2
            if not dead:
                keep.append(trk)
        self.tracks = keep

    def _merge(self):
        """
        Two confirmed tracks inside each other's gate for TRACK_MERGE_N CPIs in a row
        are one target. Delete the younger (the higher ID).
        pair_n counts the streaks.
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
        # A pair that is not close this CPI starts over (it is not in pair_n)
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
