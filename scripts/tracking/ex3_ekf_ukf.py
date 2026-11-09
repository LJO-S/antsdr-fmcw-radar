import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import chi2
import kf
from filterpy.kalman import UnscentedKalmanFilter
from filterpy.kalman import MerweScaledSigmaPoints
from filterpy.kalman import unscented_transform

# Simulation parameters
SEED = 42
N_RUNS = 200
STARTUP_S = 5.0  # NEES averaged apart before (track start) and after this
NEES_DOF = 4  # state dimension
DT = 0.04
T_END = 10.0  # at 15 m/s the target stays 150-450 m out, |theta| < ~35 deg
SR = 0.7
SV = 0.5
SA_TRUE = 5.0
SA_FILTER = 5.0
THETA_0 = 5  # start angle [deg], > 0 towards antenna B
R0 = 300  # start range [m]
SPEED = 15.0  # [m/s]
HEADING_DEG = None  # velocity direction relative to the line of sight: 0 = receding,
# 90 = sideways (cross-range, the hard case for the angle), 180 = approaching.
# None = uniform per run: a fair NEES needs the true sideways velocity drawn with the
# spread init() assumes (a fixed 90 is the same 1.5 sigma error in every run)
SV_ACROSS = SPEED / np.sqrt(2)  # init's sideways velocity std: std of SPEED*sin(heading)


class Target2D:
    def __init__(self, x, y, vx, vy, sa, rng):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.sa = sa
        self.rng = rng

    def update(self, dt):
        """
        Update the target state with a random acceleration.
        """
        ax_noise = self.rng.normal(0, self.sa)
        ay_noise = self.rng.normal(0, self.sa)
        self.x += (self.vx + ax_noise * dt / 2) * dt
        self.y += (self.vy + ay_noise * dt / 2) * dt
        self.vx += ax_noise * dt
        self.vy += ay_noise * dt


class CvExtendedKalmanFilter:
    """
    Constant-velocity Extended Kalman filter on [x, y, vx, vy], DWNA process noise.
    Interface as TrackFilter (Phase 7 guide 6.2): init, predict(dt), innovation, update.
    z is always the radar's [r, theta, r_dot].
    """

    def __init__(self, sa):
        # Process noise: std of the random acceleration [m/s^2]
        self.sa = sa

    def init(self, z, R):
        r, theta, r_dot = z
        s, c = np.sin(theta), np.cos(theta)
        # State vector [x, y, vx, vy] and its covariance from one detection
        self.x = np.array(
            [
                r * s,
                r * c,
                r_dot * s,
                r_dot * c,
            ]
        )  # [x, y, vx, vy]

        # Columns: along the line of sight (s, c) and across it (c, -s)
        Rot = np.array([[s, c], [c, -s]])
        sr, sth, sv = np.sqrt(np.diag(R))

        # Diagonal in the (along, across) frame, rotated into (x, y)
        P_pos = Rot @ np.diag([sr**2, (r * sth) ** 2]) @ Rot.T
        P_vel = Rot @ np.diag([sv**2, SV_ACROSS**2]) @ Rot.T

        self.P = np.zeros((4, 4))
        self.P[:2, :2] = P_pos
        self.P[2:, 2:] = P_vel

        # Measurement noise covariance: (r, theta, r_dot)
        self.R = R
        self.dim_z = 3

    def predict(self, dt):
        # State transition matrix for constant velocity
        F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])
        self.x = F @ self.x

        # Process noise covariance
        Q = self.process_noise(dt)
        self.P = F @ self.P @ F.T + Q

    def process_noise(self, dt):
        """
        Process noise covariance for constant-velocity model with random acceleration.
        """
        Q = self.sa**2 * np.array(
            [
                [dt**4 / 4, 0, dt**3 / 2, 0],
                [0, dt**4 / 4, 0, dt**3 / 2],
                [dt**3 / 2, 0, dt**2, 0],
                [0, dt**3 / 2, 0, dt**2],
            ]
        )
        return Q

    def jacobian_h(self, x):
        """
        Jacobian of the radar measurement function h(x) at state x.
        From TI's guide: "Tracking radar targets with multiple reflection points"
        """
        x_, y, vx, vy = x
        r2 = x_**2 + y**2
        r = np.sqrt(r2)

        # Avoid division by zero
        if r < 1e-6:
            raise ValueError("Division by zero in jacobian_h")

        H = np.zeros((3, 4))
        H[0, 0] = x_ / r
        H[0, 1] = y / r
        H[1, 0] = y / r2
        H[1, 1] = -x_ / r2
        H[2, 0] = y * (vx * y - vy * x_) / (r2 * r)
        H[2, 1] = x_ * (vy * x_ - vx * y) / (r2 * r)
        H[2, 2] = x_ / r
        H[2, 3] = y / r

        return H

    def innovation(self, z, R):
        """
        Residual y and its covariance S for measurement z, without updating (for gating)
        """
        z = np.asarray(z, dtype=float)[: self.dim_z]
        # Measurement residual
        y = z - h(self.x)
        # Normalize angle to [-pi, pi]
        y[1] = (y[1] + np.pi) % (2 * np.pi) - np.pi
        # Residual covariance
        H = self.jacobian_h(self.x)
        S = H @ self.P @ H.T + R
        return y, S

    def update(self, z, R):
        """
        Update the state and covariance with measurement z.
        Returns the NIS value for gating.
        """
        y, S = self.innovation(z, R)
        H = self.jacobian_h(self.x)
        # Kalman gain K = P H' S^-1, solved instead of inverted (S, P symmetric: K' = S^-1 H P)
        K = np.linalg.solve(S, H @ self.P).T
        # A-posteriori state estimate
        self.x += K @ y
        # A-posteriori covariance, Joseph form: stays symmetric and positive under rounding
        I_KH = np.eye(4) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T
        return kf.d2(y, S)


class CvUnscentedKalmanFilter(CvExtendedKalmanFilter):
    """
    Constant-velocity Unscented Kalman filter on [x, y, vx, vy], DWNA process noise.
    Interface as TrackFilter (Phase 7 guide 6.2): init, predict(dt), innovation, update.
    z is always the radar's [r, theta, r_dot].
    """

    def __init__(self, sa, alpha=0.3):
        super().__init__(sa)
        # Rule of thumb (Van der Merwe, Labbe 10.6.3):
        # beta = 2 for Gaussian distributions
        # kappa = 3 - n (or 0), where n = number-of-dimensions
        # 0 < alpha <= 1.
        # Points sit at +-alpha*sqrt(n + kappa) sigma; mean and
        # covariance are right to 2nd order for any alpha, alpha only scales higher orders.
        # Small alpha -> center weight very negative, P can go non-PSD.
        points = MerweScaledSigmaPoints(n=4, alpha=alpha, beta=2.0, kappa=-1)
        self.ukf = UnscentedKalmanFilter(
            dim_x=4,
            dim_z=3,
            fx=self.fx,
            hx=h,
            dt=DT,
            points=points,
            residual_z=self.residual_z,
        )

    def fx(self, x, dt):
        """
        State transition function for constant velocity.
        """
        F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])
        return F @ x

    def residual_z(self, a, b):
        """
        Residual function for radar measurements, handling angle wrapping.
        """
        y = a - b
        y[1] = (y[1] + np.pi) % (2 * np.pi) - np.pi
        return y

    def init(self, z, R):
        super().init(z, R)
        self.ukf.x = self.x.copy()
        self.ukf.P = self.P.copy()

    def innovation(self, z, R):
        """
        Residual y and its covariance S from the sigma points, without updating (for gating).
        The same S as update() uses, not the EKF's linearized one.
        """
        sigmas_h = h(self.ukf.points_fn.sigma_points(self.ukf.x, self.ukf.P))
        zp, S = unscented_transform(
            sigmas_h, self.ukf.Wm, self.ukf.Wc, R, residual_fn=self.residual_z
        )
        y = self.residual_z(np.asarray(z, dtype=float), zp)
        return y, S

    def predict(self, dt):
        self.ukf.Q = self.process_noise(dt)
        self.ukf.predict(dt=dt)
        self.x, self.P = self.ukf.x.copy(), self.ukf.P.copy()

    def update(self, z, R):
        self.ukf.update(z, R=R)
        # Cholesky errors? symmetrize
        self.ukf.P = (self.ukf.P + self.ukf.P.T) / 2
        self.x, self.P = self.ukf.x.copy(), self.ukf.P.copy()
        # Compute NIS
        return kf.d2(self.ukf.y, self.ukf.S)


def h(x):
    """
    Radar measurement function:
    [x, y, vx, vy] -> [r, theta, r_dot]
    """
    x_ = x[..., 0]
    y = x[..., 1]
    vx = x[..., 2]
    vy = x[..., 3]
    r = np.sqrt(x_**2 + y**2)
    r_dot = (x_ * vx + y * vy) / r
    theta = np.arctan2(x_, y)
    return np.stack([r, theta, r_dot], axis=-1)


def sigma_theta(snr_db, theta):
    """
    guide 9.1: 1 / (pi cos(theta) sqrt(SNR_ch)), SNR_ch linear
    """
    snr = 10 ** (snr_db / 10)
    return 1 / (np.pi * np.cos(theta) * np.sqrt(snr))


def make_scenario(snr_db, seed=SEED, heading_deg=HEADING_DEG):
    """
    One constant-velocity target with random acceleration, measured as (r, theta, v_r).
    Returns truth (n_steps, 4), meas (n_steps, 3) and sigma (n_steps, 3), the per-step
    measurement noise std (sigma_theta depends on the true theta).
    heading_deg None: uniform, drawn from the seed.
    """
    ss = np.random.SeedSequence(seed).spawn(3)
    if heading_deg is None:
        heading_deg = np.random.default_rng(ss[2]).uniform(0, 360)

    # Start state: position from (R0, theta0); velocity at heading_deg to the line of sight
    theta0 = np.radians(THETA_0)
    phi = theta0 + np.radians(heading_deg)  # velocity direction, same convention as theta
    x0, y0 = R0 * np.sin(theta0), R0 * np.cos(theta0)
    vx, vy = SPEED * np.sin(phi), SPEED * np.cos(phi)
    target = Target2D(x0, y0, vx, vy, SA_TRUE, np.random.default_rng(ss[0]))

    n_steps = round(T_END / DT)
    truth = np.zeros((n_steps, 4))
    truth[0] = [x0, y0, vx, vy]
    for n in range(1, n_steps):
        target.update(DT)
        truth[n] = [target.x, target.y, target.vx, target.vy]

    z_true = h(truth)
    # sigma_theta holds in front of the antenna only (cos(theta) < 0 gives a negative sigma)
    assert np.all(np.abs(z_true[:, 1]) < np.pi / 2), "target left the front half-plane"
    sigma = np.stack(
        [
            np.full(n_steps, SR),
            sigma_theta(snr_db, z_true[:, 1]),
            np.full(n_steps, SV),
        ],
        axis=-1,
    )
    meas = z_true + np.random.default_rng(ss[1]).normal(0, sigma)

    return truth, meas, sigma


UKF_ALPHAS = [0.1, 0.3, 0.6, 1.0]  # sigma-point spread swept for the UKF
ALPHA_UKF = 0.3  # the UKF in the plots

# alpha=a is bound as a default argument, so each lambda keeps its own value
FILTERS = {"EKF": lambda: CvExtendedKalmanFilter(SA_FILTER)}
for _a in UKF_ALPHAS:
    FILTERS[f"UKF a={_a}"] = lambda a=_a: CvUnscentedKalmanFilter(SA_FILTER, alpha=a)
PLOTTED = ("EKF", f"UKF a={ALPHA_UKF}")  # the alpha sweep is flat: printout only


def track(truth, meas, sigma, filt, full=False):
    """
    Run one filter over one scenario. Returns the NEES per step, e' P^-1 e with
    e = truth - estimate (Cartesian, so no angle wrapping).
    full=True also returns the estimates (n_steps, 4) and covariances (n_steps, 4, 4).
    """
    n_steps = len(meas)
    nees = np.zeros(n_steps)
    xs = np.zeros((n_steps, 4))
    Ps = np.zeros((n_steps, 4, 4))
    filt.init(meas[0], np.diag(sigma[0] ** 2))
    for t in range(n_steps):
        if t > 0:
            filt.predict(DT)
            filt.update(meas[t], np.diag(sigma[t] ** 2))
        e = truth[t] - filt.x
        nees[t] = e @ np.linalg.solve(filt.P, e)
        xs[t], Ps[t] = filt.x, filt.P
    return (nees, xs, Ps) if full else nees


def run(snr_db, heading_deg, make_filter, n_runs=N_RUNS, seed=SEED):
    """NEES per run and step, shape (n_runs, n_steps). make_filter() gives a fresh filter."""
    nees = []
    for i in range(n_runs):
        truth, meas, sigma = make_scenario(snr_db, seed + i, heading_deg)
        nees.append(track(truth, meas, sigma, make_filter()))
    return np.array(nees)


def plot(results, n_runs):
    """
    results: {(filter name, snr_db): nees (n_runs, n_steps)}.
    Mean NEES over the runs vs time with its 95 % band, one panel per SNR. Log scale, so
    the start-up peak and the steady state both fit.
    """
    t = np.arange(round(T_END / DT)) * DT
    lo, hi = (
        chi2.ppf([0.025, 0.975], NEES_DOF * n_runs) / n_runs
    )  # band for the run mean
    snrs = sorted({snr_db for _, snr_db in results}, reverse=True)
    fig, axes = plt.subplots(
        1, len(snrs), figsize=(5.5 * len(snrs), 4.5), sharey=True, squeeze=False
    )
    for ax, snr_db in zip(axes[0], snrs):
        for i, name in enumerate(PLOTTED):
            nees = results[(name, snr_db)]
            ax.plot(t, nees.mean(axis=0), color=f"C{i}", lw=1.2, label=name)
        ax.axhline(NEES_DOF, color="gray", ls=":", label="consistent (4)")
        ax.axhspan(lo, hi, color="gray", alpha=0.2, label="95 % band")
        ax.axvline(STARTUP_S, color="gray", lw=0.8)
        for x, text in ((STARTUP_S / 2, "start"), ((STARTUP_S + T_END) / 2, "later")):
            ax.text(x, 0.03, text, transform=ax.get_xaxis_transform(), ha="center",
                    color="gray")
        ax.set_yscale("log")
        ax.minorticks_off()
        ax.set_yticks([2, 4, 8, 16, 32], labels=["2", "4", "8", "16", "32"])
        ax.set_xlabel("t [s]")
        ax.set_title(f"{snr_db} dB")
        ax.grid(True, alpha=0.3)
    axes[0, 0].set_ylabel("mean NEES")
    axes[0, 0].legend(loc="upper right")
    heading = "random" if HEADING_DEG is None else f"{HEADING_DEG:g} deg"
    fig.suptitle(f"NEES, {n_runs} runs, heading {heading}, sa_filter={SA_FILTER}")
    fig.tight_layout()
    return fig


def plot_run(snr_db, seed=SEED, heading_deg=90.0):
    """
    One run, sideways by default (the hard case): left the x-y path (detections, truth,
    filters), right the error across the line of sight with the filter's own +-2 sigma.
    One run says nothing about consistency (use the NEES plot), but shows what the
    estimates look like.
    """
    truth, meas, sigma = make_scenario(snr_db, seed, heading_deg)
    t = np.arange(len(truth)) * DT
    # Unit vector across the line of sight at the true position, as in init()
    theta = np.arctan2(truth[:, 0], truth[:, 1])
    across = np.stack([np.cos(theta), -np.sin(theta)], axis=-1)
    fig, (ax_xy, ax_err) = plt.subplots(1, 2, figsize=(11, 4.5))
    r, th = meas[:, 0], meas[:, 1]
    ax_xy.plot(r * np.sin(th), r * np.cos(th), ".", color="gray", ms=2, label="detections")
    ax_xy.plot(truth[:, 0], truth[:, 1], "k", lw=3, alpha=0.3, label="truth")
    ax_xy.plot(*truth[0, :2], "ko", ms=5, label="start")
    for i, name in enumerate(PLOTTED):
        _, xs, Ps = track(truth, meas, sigma, FILTERS[name](), full=True)
        ax_xy.plot(xs[:, 0], xs[:, 1], color=f"C{i}", lw=1, label=name)
        err = np.einsum("ni,ni->n", xs[:, :2] - truth[:, :2], across)
        bound = 2 * np.sqrt(np.einsum("ni,nij,nj->n", across, Ps[:, :2, :2], across))
        ax_err.plot(t, err, color=f"C{i}", lw=1, label=name)
        ax_err.plot(t, bound, color=f"C{i}", ls=":", lw=1)
        ax_err.plot(t, -bound, color=f"C{i}", ls=":", lw=1)
    ax_xy.set_aspect("equal", adjustable="datalim")
    ax_xy.set_xlabel("x [m]")
    ax_xy.set_ylabel("y (boresight) [m]")
    ax_xy.set_title(f"Path, {snr_db} dB, heading {heading_deg:g} deg, seed {seed}")
    ax_xy.legend()
    ax_xy.grid(True, alpha=0.3)
    ax_err.set_xlabel("t [s]")
    ax_err.set_ylabel("error across the line of sight [m]")
    ax_err.set_title("Cross-range error and the filter's +-2 sigma (dotted)")
    ax_err.legend()
    ax_err.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


if __name__ == "__main__":
    import sys

    # Optional first argument: number of runs, e.g. `python ex3_ekf_ukf.py 50`
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else N_RUNS
    lo, hi = chi2.ppf([0.025, 0.975], NEES_DOF * n_runs) / n_runs
    k = round(STARTUP_S / DT)

    results = {}
    # The band is for one step's mean over the runs; a mean over many correlated steps
    # has a narrower one, so inside it is a weak pass
    heading = "random" if HEADING_DEG is None else f"{HEADING_DEG:g} deg"
    print(
        f"mean NEES, heading {heading}, start 0-{STARTUP_S:g} s and later "
        f"{STARTUP_S:g}-{T_END:g} s (4 = consistent, per-step 95 % band {lo:.2f}-{hi:.2f})"
    )
    for snr_db in (20, 13):
        for name, make_filter in FILTERS.items():
            nees = run(snr_db, HEADING_DEG, make_filter, n_runs)
            results[(name, snr_db)] = nees
            print(
                f"  {name} {snr_db} dB: start {nees[:, :k].mean():.2f}, "
                f"later {nees[:, k:].mean():.2f}"
            )

    plot(results, n_runs)
    plot_run(13)
    plt.show()
