"""
Shared pieces for the L0 tracking exercises: truth, radar and the (r, v) Kalman filter.
"""

import numpy as np


class Target:
    def __init__(self, r, v, sa, rng):
        self.r = r
        self.v = v
        self.sa = sa
        self.rng = rng

    def update(self, dt):
        """
        Update the target state with a random acceleration.
        """
        a_noise = self.rng.normal(0, self.sa)
        self.r += (self.v + a_noise * dt / 2) * dt
        self.v += a_noise * dt


class Radar:
    def __init__(self, sr, sv, rng):
        self.sigma = np.array([sr, sv])
        self.rng = rng

    def measure(self, target):
        """Noisy (r, v) of the target as it is now."""
        return np.array([target.r, target.v]) + self.rng.normal(0, self.sigma)


def d2(y, S):
    """Squared Mahalanobis distance of innovation y: the gate test, and the NIS once z is used."""
    return y @ np.linalg.solve(S, y)


class CvKalmanFilter:
    """
    Constant-velocity Kalman filter on [r, v], DWNA process noise.
    Interface as TrackFilter (Phase 7 guide 6.2): init, predict(dt), innovation, update.
    z is always the radar's [r, v]; with measure_v False the filter uses only z[0].
    """

    def __init__(self, sa, sr, sv, measure_v=True):
        # Process noise: std of the random acceleration [m/s^2]
        self.sa = sa

        # State vector [r, v] and its covariance; set by init()
        self.x = None
        self.P = None

        # Measurement matrix and noise covariance: (r, v) or r only
        if measure_v:
            self.H = np.eye(2)
            self.R = np.diag([sr**2, sv**2])
        else:
            self.H = np.array([[1.0, 0.0]])
            self.R = np.array([[sr**2]])
        self.dim_z = self.H.shape[0]

    def init(self, z, sv0=10.0):
        """
        Initialize the filter with the first measurement z.
        r only: v starts at 0 with std sv0 [m/s] (unknown).
        """
        z = np.asarray(z, dtype=float)
        if self.dim_z == 2:
            self.x = z.copy()
            self.P = self.R.copy()
        else:
            self.x = np.array([z[0], 0.0])
            self.P = np.diag([self.R[0, 0], sv0**2])

    def predict(self, dt):
        """
        Predict the state and covariance dt seconds ahead (dt may change every call)
        """
        # State transition matrix
        F = np.array([[1.0, dt], [0.0, 1.0]])
        # DWNA: acceleration a held over dt moves the state by G*a, so Q = sa^2 G G'
        G = np.array([dt**2 / 2, dt])
        Q = self.sa**2 * np.outer(G, G)
        # Predict the state and its covariance
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def innovation(self, z):
        """
        Residual y and its covariance S for measurement z, without updating (for gating)
        """
        z = np.asarray(z, dtype=float)[: self.dim_z]
        # Measurement residual
        y = z - self.H @ self.x
        # Residual covariance
        S = self.H @ self.P @ self.H.T + self.R
        return y, S

    def update(self, z):
        """
        Update the state with measurement z; returns the NIS of this update
        """
        y, S = self.innovation(z)
        # Kalman gain K = P H' S^-1, solved instead of inverted (S, P symmetric: K' = S^-1 H P)
        K = np.linalg.solve(S, self.H @ self.P).T
        # A-posteriori state estimate
        self.x = self.x + K @ y
        # A-posteriori covariance, Joseph form: stays symmetric and positive under rounding
        I_KH = np.eye(2) - K @ self.H
        self.P = I_KH @ self.P @ I_KH.T + K @ self.R @ K.T
        return d2(y, S)


def cross_check(dt, sa_filter, sr, sv, sa_true, num_steps, seed):
    """
    Same measurements through our filter and FilterPy's (built independently).
    Returns the max abs difference in x, P and NIS over the run.
    """
    from filterpy.kalman import KalmanFilter
    from filterpy.common import Q_discrete_white_noise

    rng = np.random.default_rng(seed)
    radar = Radar(sr=sr, sv=sv, rng=rng)
    target = Target(r=50.0, v=-3.0, sa=sa_true, rng=rng)

    ours = CvKalmanFilter(sa=sa_filter, sr=sr, sv=sv)
    ref = KalmanFilter(dim_x=2, dim_z=2)
    ref.F = np.array([[1.0, dt], [0.0, 1.0]])
    ref.H = np.eye(2)
    ref.R = np.diag([sr**2, sv**2])
    ref.Q = Q_discrete_white_noise(dim=2, dt=dt, var=sa_filter**2)  # DWNA

    z = radar.measure(target)
    ours.init(z)
    ref.x = z.reshape(2, 1).copy()  # FilterPy keeps x as a column
    ref.P = ref.R.copy()

    dx = dp = dnis = 0.0
    for _ in range(num_steps):
        target.update(dt)
        z = radar.measure(target)
        ours.predict(dt)
        nis = ours.update(z)
        ref.predict()
        ref.update(z)
        dx = max(dx, np.max(np.abs(ours.x - ref.x.ravel())))
        dp = max(dp, np.max(np.abs(ours.P - ref.P)))
        dnis = max(dnis, abs(nis - ref.mahalanobis**2))
    return dx, dp, dnis
