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


class CvKalmanFilter:
    def __init__(self, dt, process_noise_std, sr, sv):
        self.sr = sr
        self.sv = sv

        # State vector [r, v] and its covariance; set by init()
        self.x = None
        self.P = None

        # State transition matrix
        self.F = np.array([[1, dt], [0, 1]])

        # Process noise covariance matrix
        # Note: Q is derived from the Piecewise-constant white noise acceleration model
        q = process_noise_std**2
        self.Q = q * np.array(
            [
                [dt**4 / 4, dt**3 / 2],
                [dt**3 / 2, dt**2],
            ]
        )

        # Measurement matrix
        self.H = np.array([[1, 0], [0, 1]])

        # Measurement noise covariance matrix
        self.R = np.array([[self.sr**2, 0], [0, self.sv**2]])

    def predict(self):
        """
        Predict the next state and covariance
        """
        # Predict the state
        self.x = self.F @ self.x
        # Predict the state covariance
        self.P = self.F @ self.P @ self.F.T + self.Q

    def update(self, z):
        """
        Update the state with a new measurement z
        """
        # Measurement residual
        y = z - self.H @ self.x
        assert y.shape == (2,), f"y shape is {y.shape}, expected (2,)"
        # Residual covariance
        S = self.H @ self.P @ self.H.T + self.R
        # Kalman gain
        K = (self.P @ self.H.T) @ np.linalg.inv(S)
        # A-posteriori state estimate
        self.x += K @ y
        # A-posteriori covariance estimate
        I = np.eye(self.P.shape[0])
        self.P = (I - K @ self.H) @ self.P

        # Return the NIS for this update
        nis = y @ np.linalg.solve(S, y)
        return nis

    def init(self, z):
        """
        Initialize the filter with the first measurement z
        """
        self.x = z.copy()
        self.P = self.R.copy()


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

    ours = CvKalmanFilter(dt=dt, process_noise_std=sa_filter, sr=sr, sv=sv)
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
        ours.predict()
        nis = ours.update(z)
        ref.predict()
        ref.update(z)
        dx = max(dx, np.max(np.abs(ours.x - ref.x.ravel())))
        dp = max(dp, np.max(np.abs(ours.P - ref.P)))
        dnis = max(dnis, abs(nis - ref.mahalanobis**2))
    return dx, dp, dnis
