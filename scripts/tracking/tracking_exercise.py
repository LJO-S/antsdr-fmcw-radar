import numpy as np
import matplotlib.pyplot as plt

# import filterpy later as reference

SEED = 42


# 1. Truth: one target over 20-60 s with a fixed dt of 40 ms. Draw an acceleration from N(0, sa²) each step and hold it for that step, so the truth is the same DWNA model the filter assumes. Use a seeded np.random.default_rng.
# 2. Measurements: truth plus N(0, sr²) on r and N(0, sv²) on v.
# 3. Filter: start from the first measurement with P0 = R. Each step, predict, compute y and S, record NIS = yᵀS⁻¹y before the update, then update.
# 4. Plots:
#    - NIS over time, with the single-sample 95 % band for 2 dof (0.05 to 7.38).
#    - Mean NIS, with its band chi2.ppf([0.025, 0.975], 2*N) / N.
#    - Estimate error with ±2σ from P.
# 5. Mistuning: keep the truth at sa 5 and run the filter at sa 0.5, 5 and 50.


class Target:
    def __init__(self, x0, y0, vx, vy, sa):
        self.x = x0
        self.y = y0
        self.vx = vx
        self.vy = vy
        self.sa = sa  # standard deviation of acceleration noise
        # Use generator for reproducibility
        self.rng = np.random.default_rng(SEED)

    def read(self, dt):
        x = self.x
        y = self.y
        # Update velocity with acceleration noise
        ax = self.rng.normal(0, self.sa)
        ay = self.rng.normal(0, self.sa)
        self.vx += ax * dt
        self.vy += ay * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        return np.array([x, y])


class cvKalmanFilter:
    def __init__(self, dt, process_noise_std, measurement_noise_std):
        self.dt = dt
        self.process_noise_std = process_noise_std
        self.measurement_noise_std = measurement_noise_std

        # State vector: [x, y, vx, vy]
        self.x = np.zeros((4, 1))

        # State covariance matrix
        self.P = np.eye(4)

        # State transition matrix
        self.F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])

        # Process noise covariance matrix
        # Note: Q is derived from the Piecewise-constant white noise acceleration model
        q = process_noise_std**2
        self.Q = q * np.array(
            [
                [dt**4 / 4, 0, dt**3 / 2, 0],
                [0, dt**4 / 4, 0, dt**3 / 2],
                [dt**3 / 2, 0, dt**2, 0],
                [0, dt**3 / 2, 0, dt**2],
            ]
        )

        # Measurement matrix
        self.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])

        # Measurement noise covariance matrix
        r = measurement_noise_std**2
        self.R = r * np.eye(2)

    def predict(self):
        # Predict the next state
        self.x = np.dot(self.F, self.x)
        self.P = np.dot(np.dot(self.F, self.P), self.F.T) + self.Q

    def update(self, z):
        # Update the state with a new measurement z
        y = z - np.dot(self.H, self.x)  # Measurement residual
        S = np.dot(np.dot(self.H, self.P), self.H.T) + self.R  # Residual covariance
        K = np.dot(np.dot(self.P, self.H.T), np.linalg.inv(S))  # Kalman gain

        self.x += np.dot(K, y)
        I = np.eye(self.P.shape[0])
        self.P = (I - np.dot(K, self.H)).dot(self.P)
