"""
tests/test_tracking.py

L2 tracker tests (Phase 7 guide 6.9). Filter-level tests on CvKalmanFilter;
tracker-level (a)-(k), (p)-(s) on Tracker (guide 6.6), fail with
"Tracker not implemented" until it exists. Detections synthesized from
ground truth with seeded Generator, never process_cpi.
"""

import dataclasses
import glob
from pathlib import Path

import numpy as np
from scipy import optimize, stats

import common.config as config
import common.tracking as tracking
from common.config import RadarConfig
from common.dsp import NMS_SIZE
from common.tracking import CvKalmanFilter, CvExtendedKalmanFilter

# ===================================================================================
# Assumed tracker API (guide 6.6 / 6.7). Match to tracking.py in one place
# ===================================================================================
TRACKER_CLASS = "Tracker"  # Tracker(cfg).update(detections, t, mti_en) -> list[Track]
CONFIRMED = "confirmed"  # Track.status values
TENTATIVE = "tentative"
TRACK_ATTRS = {  # Track attribute names
    "id": "id",
    "status": "status",
    "filter": "filter",  # .state = [r, v], .cov
    "hits": "hits",
    "misses": "misses",
    "last_t": "last_t",
    "age": "age",
    "snr_db": "snr_db",
    "history": "history",
    "nis": "nis",
}
CFG_FIELDS = [  # 6.7 names the tests read
    "TRACK_DELETE_S",
    "TRACK_STOP_COAST_S",
    "TRACK_CONFIRM_M",
    "TRACK_CONFIRM_N",
    "TRACK_KINEMATIC_SIGMA",
    "TRACK_KINEMATIC_WINDOW_S",
    "TRACK_MERGE_N",
    "TRACK_MAX",
    "TRACK_MIN_SPEED",
]
# ===================================================================================

SEED = 0
T0 = 1000.0  # absolute time offset: tracker must use dt, not t
DT_JITTER = (0.020, 0.050)  # s, guide 6.9
PD = 0.9
FA_PER_CPI = 2.0
SPUR_RANGES = (56.0, 168.0, 337.0, 360.0)  # m, guide 6.9 (i)
G_SEEDS = 20  # test (g): seeds per gap
G_SHARE = 0.7  # test (g): share of seeds that must behave
FIELD_SEEDS = 30  # tests (p)-(s): seeds
FIELD_SHARE = 0.9  # tests (p)-(s): share of seeds that must behave
SPUR_SEEDS = 20  # test (i): seeds per spur and v
SPUR_T = 60.0  # test (i): s per run
SPUR_SHARE = 0.005  # test (i): max confirmed share of spur-born tracks
SPUR_LIFE = 1.0  # s, test (i): max time a spur track stays confirmed
SESSIONS = Path(__file__).resolve().parents[3] / "data" / "sessions"


class Skip(Exception):
    pass


# ===================================================================================
# Config-derived constants
# ===================================================================================
def coupling(a_cfg):
    return a_cfg.CHIRP_FC_HZ * a_cfg.T_EFF / a_cfg.CHIRP_BW_HZ


def range_bin(a_cfg):
    return config.c / (2 * a_cfg.CHIRP_BW_HZ)


def vel_bin(a_cfg):
    lam = config.c / a_cfg.CHIRP_FC_HZ
    return lam / (2 * a_cfg.LEGS_PER_PERIOD * a_cfg.T_EFF * a_cfg.CHIRP_REPS)


def wrap_v(a_v, a_vmax):
    return (a_v + a_vmax) % (2 * a_vmax) - a_vmax


def triangle_cfg():
    return dataclasses.replace(RadarConfig(), TRIANGLE_EN=True)


def kin_sigma_d(a_cfg, a_t, a_rate):
    """sigma_d of the kinematic check after a_t s at a_rate updates/s (guide 6.5)."""
    sr, sv = a_cfg.TRACK_STD_R, a_cfg.TRACK_STD_V
    return np.sqrt(12 * sr**2 / (a_rate * a_t**3) + sv**2 / (a_rate * a_t))


def kin_delay(a_cfg, a_v, a_rate):
    """Time till kinematic check decides on |v|: sigma_d = |v| / 2k (guide 6.5)."""
    gap = abs(a_v) / (2 * a_cfg.TRACK_KINEMATIC_SIGMA)
    return optimize.brentq(lambda t: kin_sigma_d(a_cfg, t, a_rate) - gap, 1e-3, 1e3)


def kin_vmin(a_cfg, a_rate):
    """Slowest |v| kinematic check can decide within its window."""
    return 2 * a_cfg.TRACK_KINEMATIC_SIGMA * kin_sigma_d(a_cfg, a_cfg.TRACK_KINEMATIC_WINDOW_S, a_rate)


# ===================================================================================
# Tracker access with clear failures
# ===================================================================================
def make_tracker(a_cfg):
    cls = getattr(tracking, TRACKER_CLASS, None)
    if cls is None:
        raise AssertionError("Tracker not implemented")
    for name in CFG_FIELDS:
        if not hasattr(a_cfg, name):
            raise AssertionError(f"config field {name} missing")
    return cls(a_cfg)


def attr(a_track, a_key):
    name = TRACK_ATTRS[a_key]
    if not hasattr(a_track, name):
        raise AssertionError(f"Track attribute '{name}' missing")
    return getattr(a_track, name)


def snap(a_track):
    r, v = attr(a_track, "filter").rv()
    return {
        "id": attr(a_track, "id"),
        "status": attr(a_track, "status"),
        "r": float(r),
        "v": float(v),
    }


# ===================================================================================
# Synthetic scene
# ===================================================================================
class Path1D:
    """Piecewise-linear v(t) from knots [(t, v), ...]; r integrates it exactly."""

    def __init__(self, a_r0, a_knots):
        self.t = np.array([k[0] for k in a_knots], dtype=float)
        self.v = np.array([k[1] for k in a_knots], dtype=float)
        seg = np.diff(self.t)
        self.r = a_r0 + np.concatenate(
            [[0.0], np.cumsum(0.5 * (self.v[1:] + self.v[:-1]) * seg)]
        )

    def at(self, a_t):
        t = float(np.clip(a_t, self.t[0], self.t[-1]))
        i = min(np.searchsorted(self.t, t, side="right") - 1, len(self.t) - 2)
        dt = t - self.t[i]
        a = (self.v[i + 1] - self.v[i]) / (self.t[i + 1] - self.t[i])
        v = self.v[i] + a * dt
        return self.r[i] + self.v[i] * dt + 0.5 * a * dt * dt, v


class Target:
    """Ground-truth target alive over [t_on, t_off); kind: up, down, both, mixed."""

    def __init__(
        self, a_path, a_t_on=0.0, a_t_off=np.inf, a_snr_db=25.0, a_kind="up", a_pd=PD
    ):
        self.path = a_path
        self.t_on, self.t_off = a_t_on, a_t_off
        self.snr_db, self.kind, self.pd = a_snr_db, a_kind, a_pd

    def alive(self, a_t):
        return self.t_on <= a_t < self.t_off

    def truth(self, a_t):
        return self.path.at(a_t)


def const_v(a_r0, a_v, **kw):
    return Target(Path1D(a_r0, [(0.0, a_v), (1e4, a_v)]), **kw)


class Scene:
    """Detections per CPI from truth: noise, Pd, false alarms, optional NMS merge."""

    def __init__(
        self, a_cfg, a_rng, a_targets, a_fa=FA_PER_CPI, a_merge=False, a_extra=None
    ):
        self.cfg, self.rng, self.targets = a_cfg, a_rng, a_targets
        self.fa, self.merge = a_fa, a_merge
        self.extra = a_extra  # (prob, target index): second dot
        self.k = coupling(a_cfg)
        self.vmax = a_cfg.MAX_VELOCITY
        self.rbin, self.vbin = range_bin(a_cfg), vel_bin(a_cfg)
        self.merged_now = False

    def _det(self, a_r, a_v, a_kind, a_snr):
        sr, sv = self.cfg.TRACK_STD_R, self.cfg.TRACK_STD_V
        kind = a_kind
        if kind == "mixed":
            kind = self.rng.choice(["up", "down", "both"])
        bias = {"up": self.k * a_v, "down": -self.k * a_v, "both": 0.0}[kind]
        r = a_r + bias + self.rng.normal(0, sr)
        v = wrap_v(a_v + self.rng.normal(0, sv), self.vmax)
        return {"r": float(r), "v": float(v), "kind": str(kind), "snr_db": a_snr}

    def step(self, a_t, a_mti):
        alive = [(i, tg, *tg.truth(a_t)) for i, tg in enumerate(self.targets)]
        alive = [a for a in alive if a[1].alive(a_t)]
        drop = set()
        self.merged_now = False
        if self.merge:  # NMS: stronger of two targets in one 7x7 window wins
            for x in range(len(alive)):
                for y in range(x + 1, len(alive)):
                    ia, ta, ra, va = alive[x]
                    ib, tb, rb, vb = alive[y]
                    if (
                        abs(ra - rb) <= NMS_SIZE * self.rbin
                        and abs(wrap_v(va - vb, self.vmax)) <= NMS_SIZE * self.vbin
                    ):
                        self.merged_now = True
                        drop.add(ib if ta.snr_db >= tb.snr_db else ia)
        dets = []
        for i, tg, r, v in alive:
            if i in drop or self.rng.random() > tg.pd:
                continue
            # MTI drops a walker at 0.5-1.5 m/s by SNR (measured), inside the 2.02 m/s bin
            if a_mti and abs(v) < 0.5 * self.vbin:
                continue
            dets.append(self._det(r, v, tg.kind, tg.snr_db))
            if self.extra and i == self.extra[1] and self.rng.random() < self.extra[0]:
                off = self.rng.uniform(1, 2) * self.vbin * self.rng.choice([-1, 1])
                d = self._det(r, v + off, tg.kind, tg.snr_db - 6)
                d["v"] = float(wrap_v(v + off + self.rng.normal(0, 0.3), self.vmax))
                dets.append(d)
        for _ in range(self.rng.poisson(self.fa)):
            dets.append(
                {
                    "r": float(self.rng.uniform(0, self.cfg.MAX_RANGE)),
                    "v": float(self.rng.uniform(-self.vmax, self.vmax)),
                    "kind": "up",
                    "snr_db": float(self.rng.uniform(12, 18)),
                }
            )
        return dets


class Step:
    def __init__(self, a_t, a_tracks, a_truth, a_merged, a_dets):
        self.t, self.tracks, self.truth = a_t, a_tracks, a_truth
        self.merged, self.dets = a_merged, a_dets

    def confirmed(self):
        return [s for s in self.tracks if s["status"] == CONFIRMED]


def run(a_cfg, a_scene, a_duration, a_rng, a_dt=None, a_mti=False, a_tracker=None):
    """Drive a Tracker over the scene; a_dt None = jittered dt, else fixed."""
    trk = a_tracker or make_tracker(a_cfg)
    log, t = [], T0
    while t < T0 + a_duration:
        dets = a_scene.step(t - T0, a_mti)
        tracks = [snap(x) for x in trk.update(dets, t, a_mti)]
        truth = [
            (tg.truth(t - T0) if tg.alive(t - T0) else None) for tg in a_scene.targets
        ]
        log.append(Step(t - T0, tracks, truth, a_scene.merged_now, dets))
        t += a_dt if a_dt is not None else a_rng.uniform(*DT_JITTER)
    return log


# ===================================================================================
# Matching helpers (truth <-> confirmed track)
# ===================================================================================
R_TOL, V_TOL = 6.0, 3.0  # m, m/s: two range bins, ~1.5 velocity bins


def match(a_step, a_i):
    """ID of the confirmed track nearest to target a_i, or None."""
    tr = a_step.truth[a_i]
    if tr is None:
        return None
    best, best_c = None, np.inf
    for s in a_step.confirmed():
        dr, dv = abs(s["r"] - tr[0]), abs(s["v"] - tr[1])
        if dr < R_TOL and dv < V_TOL and dr + 5 * dv < best_c:
            best, best_c = s["id"], dr + 5 * dv
    return best


def ids_of(a_log, a_i, a_skip_merged=False):
    return [match(s, a_i) for s in a_log if not (a_skip_merged and s.merged)]


def distinct(a_ids):
    return {i for i in a_ids if i is not None}


def confirmed_ids(a_log):
    return {s["id"] for st in a_log for s in st.confirmed()}


def rms_range_error(a_log, a_i, a_id):
    e = []
    for st in a_log:
        tr = st.truth[a_i]
        for s in st.confirmed():
            if s["id"] == a_id and tr is not None:
                e.append(s["r"] - tr[0])
    assert e, "no confirmed samples to score"
    return float(np.sqrt(np.mean(np.square(e)))), float(np.mean(e))


def first_confirmed_t(a_log, a_i):
    for s in a_log:
        if match(s, a_i) is not None:
            return s.t
    return None


# ===================================================================================
# Field-case helpers (tests (p)-(s))
# ===================================================================================
class PassPath:
    """Straight line past radar at distance a_d, speed a_vx along it, closest at a_t_pass.
    In (r, v): r = hypot(d, x), v = dr/dt = vx * x / r (v < 0 approaching)."""

    def __init__(self, a_d, a_vx, a_t_pass):
        self.d, self.vx, self.t_pass = a_d, a_vx, a_t_pass

    def at(self, a_t):
        x = self.vx * (a_t - self.t_pass)
        r = float(np.hypot(self.d, x))
        return r, self.vx * x / r


class SpurPath:
    """Spur: fixed r with nonzero v (never moves, whatever v says)."""

    def __init__(self, a_r, a_v):
        self.r, self.v = a_r, a_v

    def at(self, a_t):
        return self.r, self.v


class WanderPath:
    """a_base plus coloured range error, AR(1) std a_rms, correlation a_tau:
    strongest reflection wandering over car body. Only r moves; v stays clean
    Doppler, so range slope and v disagree as on real car."""

    GRID = 0.01  # s

    def __init__(self, a_base, a_rms, a_tau, a_rng, a_t_end):
        self.base = a_base
        n = int(a_t_end / self.GRID) + 2
        a = np.exp(-self.GRID / a_tau)
        w = np.empty(n)
        w[0] = a_rms * a_rng.normal()
        e = a_rng.normal(0, a_rms * np.sqrt(1 - a * a), n)
        for k in range(1, n):
            w[k] = a * w[k - 1] + e[k]
        self.t, self.w = np.arange(n) * self.GRID, w

    def at(self, a_t):
        r, v = self.base.at(a_t)
        return r + float(np.interp(a_t, self.t, self.w)), v


def kept_id(a_log, a_i):
    """Target a_i has one confirmed ID over run, matched on last step."""
    ids = ids_of(a_log, a_i)
    return len(distinct(ids)) == 1 and ids[-1] is not None


def share_of(a_fn, a_n=FIELD_SEEDS):
    """(share of seeds where a_fn(seed) is True, the failing seeds)."""
    bad = [seed for seed in range(SEED, SEED + a_n) if not a_fn(seed)]
    return 1 - len(bad) / a_n, bad


def assert_share(a_name, a_fn, a_n=FIELD_SEEDS, a_share=FIELD_SHARE):
    share, bad = share_of(a_fn, a_n)
    assert share >= a_share, f"{a_name}: {share:.0%} of {a_n} seeds, failed {bad}"


# ===================================================================================
# Filter-level tests (CvKalmanFilter, CvExtendedKalmanFilter)
# ===================================================================================
def test_filter_dwna_nis_consistent():
    """(j) mean NIS under DWNA truth in 95 % chi-square band for 2 dof."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    sa, sr, sv = cfg.TRACK_STD_A, cfg.TRACK_STD_R, cfg.TRACK_STD_V
    r, v = 300.0, 5.0
    f = CvKalmanFilter(cfg)
    f.init({"r": r, "v": v, "kind": "both"})
    burn, n_use, nis = 20, 3000, []
    for i in range(burn + n_use):
        dt = rng.uniform(*DT_JITTER)
        a = rng.normal(0, sa)
        r += v * dt + 0.5 * a * dt * dt
        v += a * dt
        f.predict(dt)
        det = {
            "r": r + rng.normal(0, sr),
            "v": v + rng.normal(0, sv),
            "kind": "both",
        }
        f.update(det)
        if i >= burn:
            nis.append(f.nis)
    mean = float(np.mean(nis))
    lo = stats.chi2.ppf(0.025, 2 * n_use) / n_use
    hi = stats.chi2.ppf(0.975, 2 * n_use) / n_use
    assert lo < mean < hi, f"mean NIS {mean:.3f} outside [{lo:.3f}, {hi:.3f}]"


def test_filter_kind_init_no_bias():
    """(k, filter) track started from noiseless up/down/both detection has no k*v bias."""
    cfg = RadarConfig()
    k = coupling(cfg)
    r, v = 400.0, -60.0
    for kind, bias in (("up", k * v), ("down", -k * v), ("both", 0.0)):
        f = CvKalmanFilter(cfg)
        f.init({"r": r + bias, "v": v, "kind": kind})
        assert abs(f.state[0] - r) < 1e-9, (kind, f.state[0] - r)
        assert abs(f.state[1] - v) < 1e-9, (kind, f.state[1] - v)


def test_filter_kinds_mixed_no_bias():
    """(k, filter) mean range error is ~0 for up-only, down-only and mixed kinds."""
    cfg = RadarConfig()
    sr, sv = cfg.TRACK_STD_R, cfg.TRACK_STD_V
    k, v = coupling(cfg), -60.0  # k*v = 0.7 m: size of sr
    for mode in ("up", "down", "both", "mixed"):
        rng = np.random.default_rng(SEED)
        r = 700.0
        f = CvKalmanFilter(cfg)
        errs = []
        for i in range(300):
            dt = rng.uniform(*DT_JITTER)
            r += v * dt
            kind = rng.choice(["up", "down", "both"]) if mode == "mixed" else mode
            bias = {"up": k * v, "down": -k * v, "both": 0.0}[kind]
            det = {
                "r": r + bias + rng.normal(0, sr),
                "v": v + rng.normal(0, sv),
                "kind": str(kind),
            }
            if i == 0:
                f.init(det)
                continue
            f.predict(dt)
            f.update(det)
            if i >= 30:
                errs.append(f.state[0] - r)
        bias_est = float(np.mean(errs))
        assert abs(bias_est) < 0.25, f"{mode}: mean range error {bias_est:.3f} m"


def test_filter_velocity_innovation_wraps():
    """Detection wrapped across +-MAX_VELOCITY gives small v innovation."""
    cfg = RadarConfig()
    vmax = cfg.MAX_VELOCITY
    for v0, v_true in ((vmax - 0.5, vmax + 0.3), (-vmax + 0.5, -vmax - 0.3)):
        for kind in ("up", "both"):
            f = CvKalmanFilter(cfg)
            f.init({"r": 100.0, "v": v0, "kind": "both"})
            f.predict(0.03)
            v_pred = float(f.state[1])
            bias = coupling(cfg) * v_true if kind == "up" else 0.0
            det = {"r": f.state[0] + bias, "v": wrap_v(v_true, vmax), "kind": kind}
            y, S = f.innovation(det)
            want = v_true - v_pred
            assert abs(y[1] - want) < 1e-6, (v0, kind, y[1], want)
            assert y @ np.linalg.solve(S, y) < 9.21, (v0, kind)


def ekf_c(a_cfg, a_kind):
    """Range coupling of a detection kind, from config (independent of the filter)."""
    return {"up": coupling(a_cfg), "down": -coupling(a_cfg)}.get(a_kind, 0.0)


def ekf_sigma(a_cfg, a_snr_db, a_theta_deg, a_kind):
    """Measurement noise std [r, theta, v_r] of a detection (guide 9.1), rebuilt from config."""
    cos_th = max(np.cos(np.radians(a_theta_deg)), 0.5)
    s_th = 1.0 / (np.pi * cos_th * np.sqrt(10 ** (a_snr_db / 10)))
    if a_kind == "both":
        s_th /= np.sqrt(2)
    s_th = max(s_th, np.radians(a_cfg.TRACK_STD_THETA_MIN_DEG))
    return np.array([a_cfg.TRACK_STD_R, s_th, a_cfg.TRACK_STD_V])


def test_ekf_jacobian_finite_difference():
    """EKF jacobian_h equals central finite differences of h, for c = +k, -k, 0."""
    cfg = RadarConfig()
    f = CvExtendedKalmanFilter(cfg)
    x = np.array([30.0, 80.0, 3.0, -5.0])
    eps = 1e-6
    for kind in ("up", "down", "both"):
        c = ekf_c(cfg, kind)
        H = f.jacobian_h(x, c)
        H_fd = np.empty((3, 4))
        for i in range(4):
            d = np.zeros(4)
            d[i] = eps
            H_fd[:, i] = (f.h(x + d, c) - f.h(x - d, c)) / (2 * eps)
        assert np.allclose(H, H_fd, rtol=1e-5, atol=1e-8), (kind, np.abs(H - H_fd).max())
    # coupling must be live: "up" and "down" range rows differ
    up, down = ekf_c(cfg, "up"), ekf_c(cfg, "down")
    assert not np.allclose(f.jacobian_h(x, up)[0], f.jacobian_h(x, down)[0])


EKF_DT = (0.055, 0.075)  # s, TDM rate (guide 9.2)
EKF_SA_TRUE = 5.0  # true random accel [m/s^2]; the filter runs TRACK_STD_A_XY
EKF_SPEED = 15.0  # m/s
EKF_THETA0_DEG = 5.0


def ekf_nees(a_snr_db, a_r0, a_t_end, a_runs=200, a_kind="up", a_seed=SEED):
    """
    NEES of CvExtendedKalmanFilter, shape (n_runs, n_steps), and time of every step.
    Port of ex3_ekf_ukf.py: (x, y) target, DWNA truth, random heading per run (fixed
    heading = same sideways-velocity error every run), measured as (r, theta, v_r)
    with filter's own R. kind "up" so range coupling is live.
    """
    cfg = RadarConfig()
    n_steps = int(a_t_end / np.mean(EKF_DT))
    nees = np.zeros((a_runs, n_steps))
    times = np.zeros((a_runs, n_steps))
    th0 = np.radians(EKF_THETA0_DEG)
    for i in range(a_runs):
        ss = np.random.SeedSequence(a_seed + i).spawn(3)
        rng_h, rng_t, rng_m = (np.random.default_rng(s) for s in ss)
        phi = th0 + np.radians(rng_h.uniform(0, 360))  # velocity direction
        truth = np.array(
            [
                a_r0 * np.sin(th0),
                a_r0 * np.cos(th0),
                EKF_SPEED * np.sin(phi),
                EKF_SPEED * np.cos(phi),
            ]
        )
        f = CvExtendedKalmanFilter(cfg)
        c = ekf_c(cfg, a_kind)
        t = 0.0
        for n in range(n_steps):
            if n > 0:
                dt = rng_t.uniform(*EKF_DT)
                a = rng_t.normal(0, EKF_SA_TRUE, 2)
                truth[:2] += (truth[2:] + a * dt / 2) * dt
                truth[2:] += a * dt
                t += dt
            # Noiseless detection gives the per-step noise std, as the filter will see it
            z_true = f.h(truth, c)
            assert abs(z_true[1]) < np.pi / 2, "target left the front half-plane"
            det = {
                "r": z_true[0],
                "theta": np.degrees(z_true[1]),
                "v": z_true[2],
                "kind": a_kind,
                "snr_db": a_snr_db,
            }
            sigma = ekf_sigma(cfg, a_snr_db, det["theta"], a_kind)
            z = z_true + rng_m.normal(0, sigma)
            det.update(r=z[0], theta=np.degrees(z[1]), v=z[2])
            if n == 0:
                f.init(det)
            else:
                f.predict(dt)
                f.update(det)
            e = truth - f.x
            nees[i, n] = e @ np.linalg.solve(f.P, e)
            times[i, n] = t
    return nees, times


def test_ekf_nees_straight_100m_20db():
    """(l) 100 m, 20 dB: mean NEES <= ~4 (under 4 is the price of sa 10)."""
    nees, times = ekf_nees(20.0, 100.0, 5.0)
    mean = float(nees[times >= 1.0].mean())  # after the start-up bump
    assert mean <= 4.5, f"mean NEES {mean:.2f} > 4.5"


def test_ekf_nees_long_range_300m_13db():
    """(n) 300 m, 13 dB, random heading: mean NEES below the 95 % band's upper edge after 7.5 s."""
    runs = 200
    nees, times = ekf_nees(13.0, 300.0, 10.0, a_runs=runs)
    # Wrong-sign starts (guide 3.4) take ~7.5 s to recover at the TDM rate
    mean = float(nees[times >= 7.5].mean())
    # One-sided: truth sa 5 vs filter sa 10, so NEES sits under 4 by design.
    # Overconfidence (mean above hi) is the failure that matters.
    hi = stats.chi2.ppf(0.975, 4 * runs) / runs
    assert mean < hi, f"mean NEES {mean:.2f} above {hi:.2f}"


def test_ekf_init_round_trip():
    """EKF init from a detection: h(x0) gives the detection back, for every kind."""
    cfg = RadarConfig()
    f = CvExtendedKalmanFilter(cfg)
    for kind in ("up", "down", "both"):
        det = {"r": 100.0, "theta": 25.0, "v": 12.0, "kind": kind, "snr_db": 20.0}
        c = ekf_c(cfg, kind)
        z = np.array([100.0, np.radians(25.0), 12.0])  # theta deg -> rad
        f.init(det)
        assert np.allclose(f.h(f.x, c), z, atol=1e-9), (kind, f.h(f.x, c) - z)
        # position without coupling bias
        assert abs(np.hypot(*f.x[:2]) - (100.0 - c * 12.0)) < 1e-9, kind


def test_ekf_missing_snr_uses_nominal():
    """Missing, None or NaN snr_db gives the 13 dB R (old recordings carry none)."""
    cfg = RadarConfig()
    det = {"r": 100.0, "theta": 25.0, "v": 12.0, "kind": "both", "snr_db": 13.0}
    ref = CvExtendedKalmanFilter(cfg)
    ref.init(det)
    for snr in ("missing", None, float("nan"), float("-inf")):
        d = {k: v for k, v in det.items() if k != "snr_db"}
        if snr != "missing":
            d["snr_db"] = snr
        f = CvExtendedKalmanFilter(cfg)
        f.init(d)
        assert np.all(np.isfinite(f.P)), snr
        assert np.allclose(f.P, ref.P), snr
        f.update(d)
        assert np.isfinite(f.nis), snr


# EKF reference (oracle): per-filter code on plain arrays, independent of the filter's
# own predict/update so it stays valid once those delegate to the batched versions.
# R and c come from the config helpers above, h / jacobian_h from the filter.
def ekf_ref_R(a_cfg, a_det):
    sigma = ekf_sigma(a_cfg, a_det["snr_db"], a_det["theta"], a_det["kind"])
    return np.diag(sigma**2)


def ekf_ref_predict(a_cfg, a_x, a_P, a_dt):
    F = np.array([[1, 0, a_dt, 0], [0, 1, 0, a_dt], [0, 0, 1, 0], [0, 0, 0, 1]])
    G = np.array(
        [
            [a_dt**4 / 4, 0, a_dt**3 / 2, 0],
            [0, a_dt**4 / 4, 0, a_dt**3 / 2],
            [a_dt**3 / 2, 0, a_dt**2, 0],
            [0, a_dt**3 / 2, 0, a_dt**2],
        ]
    )
    return F @ a_x, F @ a_P @ F.T + a_cfg.TRACK_STD_A_XY**2 * G


def ekf_ref_innovation(a_cfg, a_f, a_x, a_P, a_det):
    """(y, S, H, R) of one detection against state (a_x, a_P); a_f only supplies h, H."""
    c = ekf_c(a_cfg, a_det["kind"])
    R = ekf_ref_R(a_cfg, a_det)
    z = np.array([a_det["r"], np.radians(a_det["theta"]), a_det["v"]])
    vmax = a_cfg.MAX_VELOCITY
    y = z - a_f.h(a_x, c)
    y[1] = (y[1] + np.pi) % (2 * np.pi) - np.pi
    y[2] = (y[2] + vmax) % (2 * vmax) - vmax
    H = a_f.jacobian_h(a_x, c)
    return y, H @ a_P @ H.T + R, H, R


def ekf_ref_update(a_cfg, a_f, a_x, a_P, a_det):
    """(x, P, nis, v, r): v = predicted v_r + wrapped innovation, r = z_r - c*v."""
    c = ekf_c(a_cfg, a_det["kind"])
    y, S, H, R = ekf_ref_innovation(a_cfg, a_f, a_x, a_P, a_det)
    K = np.linalg.solve(S, H @ a_P).T
    I_KH = np.eye(4) - K @ H
    P = I_KH @ a_P @ I_KH.T + K @ R @ K.T
    v = a_f.h(a_x, c)[2] + y[2]
    return a_x + K @ y, P, y @ np.linalg.solve(S, y), v, a_det["r"] - c * v


def ekf_ref_d2_matrix(a_cfg, a_f, a_states, a_dets):
    """(n_f, n_d) d^2 by looping; a_states = [(x, P)]."""
    D = np.zeros((len(a_states), len(a_dets)))
    for i, (x, P) in enumerate(a_states):
        for j, det in enumerate(a_dets):
            y, S, _, _ = ekf_ref_innovation(a_cfg, a_f, x, P, det)
            D[i, j] = y @ np.linalg.solve(S, y)
    return D


def ekf_ref_pair_d2(a_cfg, a_f, a_states):
    """
    (n, n) d^2 between predicted measurements by looping; a_states = [(x, P, R)], R the
    last detection's. Geometric range (c = 0). Assumed form: S = H_a P_a H_a' + H_b P_b H_b' + R_a + R_b.
    """
    n, vmax = len(a_states), a_cfg.MAX_VELOCITY
    D = np.zeros((n, n))
    for i, (xa, Pa, Ra) in enumerate(a_states):
        for j, (xb, Pb, Rb) in enumerate(a_states):
            y = a_f.h(xa, 0.0) - a_f.h(xb, 0.0)
            y[1] = (y[1] + np.pi) % (2 * np.pi) - np.pi
            y[2] = (y[2] + vmax) % (2 * vmax) - vmax
            Ha, Hb = a_f.jacobian_h(xa, 0.0), a_f.jacobian_h(xb, 0.0)
            S = Ha @ Pa @ Ha.T + Hb @ Pb @ Hb.T + Ra + Rb
            D[i, j] = y @ np.linalg.solve(S, y)
    return D


def test_ekf_matches_reference():
    """EKF predict / update / innovation equal the reference to 1e-10, mixed kinds."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    f = CvExtendedKalmanFilter(cfg)
    truth = np.array([30.0, 80.0, 3.0, -5.0])
    kinds = ("up", "down", "both")

    def make_det(a_i):
        kind, snr = kinds[a_i % 3], 10.0 + 2.0 * (a_i % 7)
        z = f.h(truth, ekf_c(cfg, kind))
        det = {"r": z[0], "theta": np.degrees(z[1]), "v": z[2], "kind": kind,
               "snr_db": snr}
        sigma = ekf_sigma(cfg, snr, det["theta"], kind)
        z = z + rng.normal(0, sigma)
        det.update(r=z[0], theta=np.degrees(z[1]), v=z[2])
        return det

    f.init(make_det(0))
    x, P = f.x.copy(), f.P.copy()
    for i in range(1, 40):
        dt = rng.uniform(*EKF_DT)
        truth[:2] += truth[2:] * dt
        det = make_det(i)
        f.predict(dt)
        x, P = ekf_ref_predict(cfg, x, P, dt)
        assert np.allclose(f.x, x, rtol=1e-10, atol=1e-10), ("predict x", i)
        assert np.allclose(f.P, P, rtol=1e-10, atol=1e-10), ("predict P", i)
        y, S, _, _ = ekf_ref_innovation(cfg, f, x, P, det)
        y_f, S_f = f.innovation(det)
        assert np.allclose(y_f, y, rtol=1e-10, atol=1e-10), ("innovation y", i)
        assert np.allclose(S_f, S, rtol=1e-10, atol=1e-10), ("innovation S", i)
        f.update(det)
        x, P, nis, _, _ = ekf_ref_update(cfg, f, x, P, det)
        assert np.allclose(f.x, x, rtol=1e-10, atol=1e-10), ("update x", i)
        assert np.allclose(f.P, P, rtol=1e-10, atol=1e-10), ("update P", i)
        assert abs(f.nis - nis) < 1e-10 * max(1.0, nis), ("nis", i)


# ===================================================================================
# Tracker-level tests
# ===================================================================================
def test_track_api():
    """Tracks expose the guide 6.6 content (id, status, filter, hits, ...)."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    scene = Scene(cfg, rng, [const_v(100.0, 8.0, a_pd=1.0)], a_fa=0)
    trk = make_tracker(cfg)
    log = run(cfg, scene, 2.0, rng, a_tracker=trk)
    tracks = trk.update([], T0 + 2.05, False)
    assert tracks, "no track after 2 s of a Pd 1 target"
    for key in TRACK_ATTRS:
        attr(tracks[0], key)
    f = attr(tracks[0], "filter")
    assert np.asarray(f.state).size == 2 and np.asarray(f.cov).shape == (2, 2)
    assert attr(tracks[0], "status") in (CONFIRMED, TENTATIVE)
    ids = [s["id"] for st in log for s in st.tracks]
    assert len(set(ids)) == 1, f"one clean target made ids {sorted(set(ids))}"


def test_a_one_target():
    """(a) one confirmed track, one ID for 20 s, range RMS below sr."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    scene = Scene(cfg, rng, [const_v(100.0, 8.0)])
    log = run(cfg, scene, 20.0, rng)
    ids = confirmed_ids(log)
    assert len(ids) == 1, f"confirmed ids {sorted(ids)}"
    tid = ids.pop()
    t_conf = first_confirmed_t(log, 0)
    assert t_conf is not None and t_conf < 2.0, f"confirmed at {t_conf}"
    after = [s for s in log if s.t >= t_conf]
    assert all(
        tid in {c["id"] for c in s.confirmed()} for s in after
    ), "track lost after confirming"
    rms, _ = rms_range_error(after, 0, tid)
    assert rms < cfg.TRACK_STD_R, f"range RMS {rms:.3f} m >= sr"


def crossing_scene(a_cfg, a_rng):
    return Scene(
        a_cfg,
        a_rng,
        [const_v(100.0, 10.0), const_v(160.0, -10.0, a_snr_db=22.0)],
        a_merge=True,
    )


def test_b_crossing_no_swap():
    """(b) targets crossing in range, 10 velocity bins apart: IDs do not swap."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    log = run(cfg, crossing_scene(cfg, rng), 8.0, rng)
    a, b = distinct(ids_of(log, 0)), distinct(ids_of(log, 1))
    assert len(a) == 1 and len(b) == 1, f"ids A {a} B {b}"
    assert a != b, "both targets share one ID"


def test_c_false_alarms_only():
    """(c) 60 s of false alarms: no confirmed track."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    log = run(cfg, Scene(cfg, rng, []), 60.0, rng)
    ids = confirmed_ids(log)
    assert not ids, f"{len(ids)} confirmed track(s) from clutter only"


def test_d_vanish_deleted_in_time():
    """(d) vanished target's track gone within TRACK_DELETE_S (+ one CPI)."""
    cfg = RadarConfig()
    delete_s = cfg.TRACK_DELETE_S if hasattr(cfg, "TRACK_DELETE_S") else None
    if delete_s is None:
        raise AssertionError("config field TRACK_DELETE_S missing")
    rng = np.random.default_rng(SEED)
    t_off = 6.0
    scene = Scene(cfg, rng, [const_v(100.0, 8.0, a_t_off=t_off)])
    log = run(cfg, scene, 10.0, rng)
    # last detection near target, from emitted stream
    t_last, tid = None, None
    for st in log:
        if st.t < t_off and st.truth[0] is not None:
            if any(abs(d["r"] - st.truth[0][0]) < R_TOL for d in st.dets):
                t_last = st.t
            if match(st, 0) is not None:
                tid = match(st, 0)
    assert tid is not None and t_last is not None, "target never confirmed"
    t_del = next(
        (s.t for s in log if s.t > t_last and tid not in {c["id"] for c in s.tracks}),
        None,
    )
    assert t_del is not None, "track never deleted"
    late = t_del - t_last - delete_s
    assert late <= DT_JITTER[1] + 1e-9, f"deleted {late:.3f} s late"


def test_e_fixed_vs_jittered_dt():
    """(e) fixed and jittered dt give same tracks: IDs, count, accuracy."""
    cfg = RadarConfig()
    res = []
    for dt in (0.035, None):
        rng = np.random.default_rng(SEED)
        log = run(cfg, crossing_scene(cfg, rng), 8.0, rng, a_dt=dt)
        a, b = distinct(ids_of(log, 0)), distinct(ids_of(log, 1))
        assert len(a) == 1 and len(b) == 1 and a != b, f"dt={dt}: A {a} B {b}"
        rms = rms_range_error(log, 0, a.pop())[0], rms_range_error(log, 1, b.pop())[0]
        res.append((len(confirmed_ids(log)), rms))
    assert res[0][0] == res[1][0], f"track counts {res[0][0]} vs {res[1][0]}"
    for a_rms, b_rms in zip(res[0][1], res[1][1]):
        assert abs(a_rms - b_rms) < 0.5 * cfg.TRACK_STD_R, res


def test_f_slow_crossing():
    """(f) slow crossing inside one NMS window: no swap, max one ID lost."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    # dv 2 velocity bins, 18 m apart: ~4.5 s inside one 7x7 window
    vb = vel_bin(cfg)
    scene = Scene(
        cfg,
        rng,
        [
            const_v(100.0, vb, a_snr_db=28.0),
            const_v(118.0, -vb, a_snr_db=22.0),
        ],
        a_merge=True,
    )
    log = run(cfg, scene, 12.0, rng)
    assert any(s.merged for s in log), "scene never merged the targets"
    strong = distinct(ids_of(log, 0, True))
    weak = distinct(ids_of(log, 1, True))
    assert not (strong & weak), f"swap: shared ids {strong & weak}"
    assert len(strong) == 1, f"stronger target ids {strong}"
    assert len(weak) <= 2, f"weaker target ids {weak}: more than one ID lost"


def walker_stop_go(a_cfg, a_gap):
    """Walker 3 m/s, decelerates to rest and back; detections cease for a_gap s."""
    vb = 0.5 * vel_bin(a_cfg)  # Scene's MTI cut
    v, a = 3.0, 2.0  # m/s, m/s^2
    t_cease = 5.0 + (v - vb) / a
    t_resume = t_cease + a_gap
    t1 = t_resume - vb / a  # accelerate from t1
    knots = [
        (0, v),
        (5.0, v),
        (5.0 + v / a, 0.0),
        (t1, 0.0),
        (t1 + v / a, v),
        (60.0, v),
    ]
    return Target(Path1D(60.0, knots)), t_cease, t_resume


def test_g_stop_and_go():
    """(g) MTI on, walker stops: 4 s gap keeps ID, 6 s gap gives new one.
    Share over G_SEEDS seeds (>= G_SHARE): one seed fragile at 2 false alarms per CPI.
    Run never confirming before or after stop = miss."""
    cfg = RadarConfig()
    for gap, same in ((4.0, True), (6.0, False)):
        ok, bad = 0, []
        for seed in range(SEED, SEED + G_SEEDS):
            rng = np.random.default_rng(seed)
            tg, t_cease, t_resume = walker_stop_go(cfg, gap)
            scene = Scene(cfg, rng, [tg])
            log = run(cfg, scene, t_resume + 5.0, rng, a_mti=True)
            # match() returns an ID, and ID 0 is falsy: compare with None
            before = [
                m for s in log if s.t < t_cease - 0.2 and (m := match(s, 0)) is not None
            ]
            after = [
                m for s in log if s.t > t_resume + 3.0 and (m := match(s, 0)) is not None
            ]
            if before and after and ((after[-1] == before[-1]) == same):
                ok += 1
            else:
                bad.append(seed)
        assert ok >= G_SHARE * G_SEEDS, (
            f"gap {gap}: {'ID kept' if same else 'new ID'} in {ok}/{G_SEEDS} seeds, "
            f"failed {bad}"
        )


def test_h_second_dot():
    """(h) a walker with a second dot 1-2 velocity bins off in 30 % of CPIs: one track."""
    cfg = RadarConfig()
    rng = np.random.default_rng(SEED)
    scene = Scene(cfg, rng, [const_v(50.0, 1.5)], a_extra=(0.3, 0))
    log = run(cfg, scene, 15.0, rng)
    ids = confirmed_ids(log)
    assert len(ids) == 1, f"confirmed ids {sorted(ids)}"
    assert max(len(s.confirmed()) for s in log) == 1


def test_i_spurs_synthetic():
    """(i) fixed-r detections at v = 2, 10, 40 m/s, Pd 1, 60 s, SPUR_SEEDS seeds per spur.
    Not "never": spur-born track passes kinematic check by chance (~3 sigma on first
    ~0.4 s), then r runs off at v, spur leaves gate, times out ~TRACK_DELETE_S later.
    So: max SPUR_SHARE of spur-born tracks confirm, none confirmed SPUR_LIFE
    (the leak, two tracks sharing the stream, did)."""
    cfg = RadarConfig()
    sr, sv = cfg.TRACK_STD_R, cfg.TRACK_STD_V
    for v in (2.0, 10.0, 40.0):
        born, conf = 0, {}  # conf: (r, seed, id) -> [first, last] t confirmed
        for r_spur in SPUR_RANGES:
            for seed in range(SEED, SEED + SPUR_SEEDS):
                rng = np.random.default_rng(seed)
                scene = Scene(cfg, rng, [], a_fa=0)  # no false alarms: every track is spur-born
                trk = make_tracker(cfg)
                t, ids = T0, set()
                while t < T0 + SPUR_T:
                    det = {
                        "r": r_spur + rng.normal(0, sr),
                        "v": v + rng.normal(0, sv),
                        "kind": "up",
                        "snr_db": 30.0,
                    }
                    for s in trk.update([det] + scene.step(t - T0, False), t, False):
                        sn = snap(s)
                        ids.add(sn["id"])
                        if sn["status"] == CONFIRMED:
                            conf.setdefault((r_spur, seed, sn["id"]), [t, t])[1] = t
                    t += rng.uniform(*DT_JITTER)
                born += len(ids)
        share = len(conf) / born
        assert share <= SPUR_SHARE, (
            f"{v} m/s: {len(conf)} of {born} spur tracks confirmed ({share:.2%})"
        )
        key, (a, b) = max(conf.items(), key=lambda kv: kv[1][1] - kv[1][0], default=(None, (0, 0)))
        assert b - a < SPUR_LIFE, f"{v} m/s: (r, seed, id) {key} confirmed {b - a:.2f} s"


def test_i_spurs_reference_sessions():
    """(i) recorded sessions 20261102-19*: no confirmed track at spur range for 1 s."""
    from offline import replay

    dirs = sorted(glob.glob(str(SESSIONS / "20261102-19*")))
    if not dirs:
        raise Skip(f"no reference sessions under {SESSIONS}")
    for d in dirs:
        cfg, _ = replay.load_session(Path(d))
        index = replay.load_index(Path(d))
        dets = replay.load_detections(Path(d))
        trk = make_tracker(cfg)
        vmin = kin_vmin(cfg, (len(index) - 1) / (index[-1][0] - index[0][0]))
        entered = {}  # (track id, spur) -> (t, r) when it came within 6 m, confirmed
        for block, (t, _off, _n) in enumerate(index):
            line = dets.get(block)
            if line is None:
                continue
            inside = {}
            for s in trk.update(line["targets"], t, bool(line["mti"])):
                sn = snap(s)
                if sn["status"] != CONFIRMED:
                    continue
                for r_spur in SPUR_RANGES:
                    if abs(sn["r"] - r_spur) >= 6.0:
                        continue
                    key = (sn["id"], r_spur)
                    t0, r0 = inside[key] = entered.get(key, (t, sn["r"]))
                    # fake target passing through moves with its v; spur stays put
                    moving = abs(sn["v"]) > vmin
                    sits = abs(sn["r"] - r0) < 0.5 * abs(sn["v"]) * (t - t0)
                    assert not (t - t0 >= 1.0 and moving and sits), (
                        f"{Path(d).name} block {block}: track {sn['id']} "
                        f"sits at {sn['r']:.1f} m (spur {r_spur})"
                    )
            entered = inside


def test_k_triangle_kinds():
    """(k) mixed up/down/both from one target: one track, no k*v range bias."""
    cfg = triangle_cfg()
    v = -40.0  # k*v = 0.46 m
    for mode in ("mixed", "up", "down"):
        rng = np.random.default_rng(SEED)
        scene = Scene(cfg, rng, [const_v(700.0, v, a_kind=mode)])
        log = run(cfg, scene, 8.0, rng)
        ids = confirmed_ids(log)
        assert len(ids) == 1, f"{mode}: confirmed ids {sorted(ids)}"
        tid = next(iter(ids))
        rms, bias = rms_range_error(log, 0, tid)
        assert abs(bias) < 0.25, f"{mode}: range bias {bias:.3f} m"
        assert rms < cfg.TRACK_STD_R, f"{mode}: range RMS {rms:.3f} m"


# ===================================================================================
# Field cases (guide 6.9, added 2026-11-12): shares over seeds, default Pd and false alarms
# ===================================================================================
def test_p_braking():
    """(p) car 15 m/s brakes at 8 m/s^2 to 3 m/s (approaching or receding, onset varies):
    one ID in >= 90 % of seeds."""
    cfg = RadarConfig()
    dur = (15.0 - 3.0) / 8.0

    def one(seed):
        rng = np.random.default_rng(seed)
        sgn = rng.choice([-1.0, 1.0])
        t_b = rng.uniform(3.0, 6.0)
        r0 = 100.0 if sgn > 0 else 400.0
        path = Path1D(
            r0, [(0, sgn * 15), (t_b, sgn * 15), (t_b + dur, sgn * 3), (1e4, sgn * 3)]
        )
        log = run(cfg, Scene(cfg, rng, [Target(path)]), t_b + dur + 5.0, rng)
        return kept_id(log, 0)

    assert_share("braking", one)


def test_q_passing():
    """(q) car 15 m/s on straight line 30 m off boresight, seen in (r, v) (7.5 m/s^2
    apparent radial acceleration at closest approach): one ID in >= 90 %. r(t) same
    for either direction along line, so only noise varies with seed."""
    cfg = RadarConfig()
    x0, speed = 100.0, 15.0

    def one(seed):
        rng = np.random.default_rng(seed)
        path = PassPath(30.0, speed, x0 / speed)
        log = run(cfg, Scene(cfg, rng, [Target(path)]), 2 * x0 / speed, rng)
        return kept_id(log, 0)

    assert_share("passing", one)


def _wander_run(a_cfg, a_seed, a_r0, a_v, a_rms, a_tau, a_t, a_mti=False):
    rng = np.random.default_rng(a_seed)
    base = Path1D(a_r0, [(0, a_v), (1e4, a_v)])
    path = WanderPath(base, a_rms, a_tau, rng, a_t)
    return run(a_cfg, Scene(a_cfg, rng, [Target(path)]), a_t, rng, a_mti=a_mti)


def test_r_coloured_range_noise():
    """(r) car 10 m/s for 60 s (either direction) with AR(1) range error, 1 m rms and
    1 s correlation, on top of sr: one ID in >= 90 % of seeds. Report only: walker 1 m/s
    with 0.5 m rms and 1 s (MTI off: sits in MTI notch), open fail-floor
    question in TODO.md."""
    cfg = RadarConfig()

    def car(seed):
        sgn = 1.0 if seed % 2 == 0 else -1.0
        log = _wander_run(cfg, seed, 100.0 if sgn > 0 else 700.0, sgn * 10.0, 1.0, 1.0, 60.0)
        return kept_id(log, 0)

    def walker(seed):
        sgn = 1.0 if seed % 2 == 0 else -1.0
        log = _wander_run(cfg, seed, 50.0 if sgn > 0 else 110.0, sgn * 1.0, 0.5, 1.0, 60.0)
        return kept_id(log, 0)

    share, bad = share_of(walker)
    print(f"      (r) walker 1 m/s, 0.5 m rms wander, 60 s: one ID in {share:.0%} of {FIELD_SEEDS} seeds, failed {bad}")
    assert_share("coloured range noise, car", car)


def test_s_spur_handover():
    """(s) walker at 1.5 and 2 m/s (either direction) hands confirmed track to a
    stronger spur of its own v (+-0.5 m/s) starting at its r; one detection while both
    share a 7x7 window. Once walker is R_TOL clear, handed-over track sits on spur
    no later than TRACK_KINEMATIC_WINDOW_S after hand-over (then window holds
    only spur data), in >= 90 % of seeds. Not kin_delay(v): window still holds
    walker's motion, so |v_mean| / 2 floor fails track once spur data fills about
    half of it, ~1.5 s at any v. From ~3 m/s gate drops spur first (same with
    check off), so only slow targets test the check. Spur-born tracks are (i)'s, not
    counted here."""
    cfg = RadarConfig()
    t_h = 5.0  # s: hand-over, after the walker confirms (6.5 delay ~1.6 s)

    def one(seed, v):
        rng = np.random.default_rng(seed)
        sgn = rng.choice([-1.0, 1.0])
        walker = const_v(100.0, sgn * v)
        r_s = walker.truth(t_h)[0]
        v_s = sgn * (v + rng.uniform(-0.5, 0.5))
        spur = Target(SpurPath(r_s, v_s), a_t_on=t_h, a_snr_db=35.0, a_pd=1.0)
        log = run(cfg, Scene(cfg, rng, [walker, spur], a_merge=True), t_h + 8.0, rng)
        cid = match(next(s for s in log if s.t >= t_h), 0)
        if cid is None:
            return False  # not confirmed at the hand-over
        on = [
            s.t
            for s in log
            if s.t >= t_h
            and abs(s.truth[0][0] - r_s) >= R_TOL
            and any(x["id"] == cid and abs(x["r"] - r_s) < R_TOL for x in s.confirmed())
        ]
        return not on or max(on) - t_h <= cfg.TRACK_KINEMATIC_WINDOW_S

    for v in (1.5, 2.0):
        assert_share(f"spur hand-over at {v} m/s", lambda seed: one(seed, v))


def test_confirm_needs_a_hit():
    """Tentative track with M of N reaching age W while coasting stays tentative
    that CPI (kinematic() passes at age W; confirm needs hit and M of N).
    Confirms on next hit. Noise-free slow target: check undecided until W."""
    cfg = RadarConfig()
    trk = make_tracker(cfg)
    r0, v, dt = 100.0, 0.3, 0.05
    w = cfg.TRACK_KINEMATIC_WINDOW_S
    i, miss_done = 0, False
    while i * dt < w + 5 * dt:
        t = T0 + i * dt
        coast = not miss_done and t - T0 >= w  # age W as tracker sees it: no detection
        dets = [] if coast else [{"r": r0 + v * i * dt, "v": v, "kind": "both", "snr_db": 25.0}]
        tracks = [snap(x) for x in trk.update(dets, t, False)]
        assert len(tracks) == 1, f"CPI {i}: {len(tracks)} tracks"
        if t - T0 < w:
            assert tracks[0]["status"] == TENTATIVE, f"CPI {i}: confirmed before age W"
        elif coast:
            assert tracks[0]["status"] == TENTATIVE, "confirmed on a CPI without a hit"
            miss_done = True
        elif miss_done:
            assert tracks[0]["status"] == CONFIRMED, "no confirm on the next hit"
            break
        i += 1
    assert miss_done, "the age-W CPI was never reached"


def confirm_delays(a_cfg, a_r0, a_v, a_n=20):
    out = []
    for seed in range(a_n):
        rng = np.random.default_rng(seed)
        scene = Scene(a_cfg, rng, [const_v(a_r0, a_v)])
        log = run(a_cfg, scene, a_cfg.TRACK_KINEMATIC_WINDOW_S + 1.0, rng)
        t = first_confirmed_t(log, 0)
        out.append(np.inf if t is None else t)
    return np.array(out)


def test_confirmation_delay():
    """Walker 1.5 m/s and car 15 m/s confirm within 1.25x the guide 6.5 delay (median; p90 < 2x)."""
    cfg = RadarConfig()
    rate = PD / np.mean(DT_JITTER)
    for name, v in (("walker", 1.5), ("car", 15.0)):
        limit = 1.25 * kin_delay(cfg, v, rate)
        d = confirm_delays(cfg, 100.0, v)
        med, p90 = float(np.median(d)), float(np.percentile(d, 90))
        assert med <= limit, f"{name}: median {med:.2f} s > {limit} s"
        assert p90 <= 2 * limit, f"{name}: p90 {p90:.2f} s > {2 * limit} s"


def _mk_dets(a_t, a_targets, a_theta=True):
    """Noise-free detections at a_t from (r0, v, t_on, t_off) tuples."""
    dets = []
    for r0, v, t_on, t_off in a_targets:
        if t_on <= a_t < t_off:
            d = {"r": r0 + v * (a_t - t_on), "v": v, "kind": "both", "snr_db": 25.0}
            if a_theta:
                d["theta"] = 10.0 + a_t
            dets.append(d)
    return dets


def test_track_num_confirmation_order():
    """num: None while tentative, 0, 1, 2 in confirmation order, never reused."""
    cfg = RadarConfig()
    trk = make_tracker(cfg)
    dt = 0.05
    # slow (kinematic check waits for W), fast, then a late one after B is gone
    scene = [
        (100.0, 0.3, 0.0, 1e9),
        (50.0, 12.0, 0.5, 6.0),
        (60.0, 9.0, 12.0, 1e9),
    ]
    first_conf, born, seen_tentative = {}, [], False
    for i in range(int(25.0 / dt)):
        t = T0 + i * dt
        dets = _mk_dets(i * dt, scene)
        if i == 100:  # lone false alarm, one CPI only
            dets.append({"r": 150.0, "v": 5.0, "kind": "both", "snr_db": 15.0})
        for tr in trk.update(dets, t, False):
            s = tr.snapshot()
            if s["id"] not in born:
                born.append(s["id"])
            if s["status"] == TENTATIVE:
                seen_tentative = True
                assert s["num"] is None, f"tentative track {s['id']} has num"
            elif s["id"] not in first_conf:
                first_conf[s["id"]] = (i, s["num"])
            else:
                assert s["num"] == first_conf[s["id"]][1], "num changed"
    assert seen_tentative
    order = sorted(first_conf, key=lambda k: first_conf[k][0])
    nums = [first_conf[k][1] for k in order]
    assert nums == list(range(len(nums))), f"nums {nums} not 0.. in confirm order"
    assert len(nums) >= 3, f"only {len(nums)} confirmed"
    assert order != sorted(order), "confirmation order equals birth order: weak test"
    assert trk.next_num == len(nums)


def test_track_theta_snapshot():
    """theta: last matched detection's; kept while coasting; None without theta."""
    cfg = RadarConfig()
    dt = 0.05
    for with_theta in (True, False):
        trk = make_tracker(cfg)
        scene = [(100.0, 8.0, 0.0, 1e9)]
        last = None
        for i in range(60):
            t = T0 + i * dt
            coast = i >= 40 and i < 43
            dets = [] if coast else _mk_dets(i * dt, scene, with_theta)
            tracks = trk.update(dets, t, False)
            assert len(tracks) == 1
            s = tracks[0].snapshot()
            want = dets[0].get("theta") if dets else last
            assert s["theta"] == want, f"CPI {i}: theta {s['theta']} != {want}"
            if dets:
                last = want
            if coast:
                assert s["coasting"]
        assert (last is not None) == with_theta


def test_track_birth_t_snapshot():
    """birth_t in the snapshot is the time of the birth CPI."""
    cfg = RadarConfig()
    trk = make_tracker(cfg)
    dt, t_on = 0.05, 0.5
    scene = [(100.0, 8.0, t_on, 1e9)]
    for i in range(40):
        t = T0 + i * dt
        tracks = trk.update(_mk_dets(i * dt, scene), t, False)
        for tr in tracks:
            s = tr.snapshot()
            assert isinstance(s["birth_t"], float)
            assert abs(s["birth_t"] - (T0 + t_on)) < 1e-9, s["birth_t"]
    assert tracks


# ===================================================================================
if __name__ == "__main__":
    tests = [
        test_filter_dwna_nis_consistent,
        test_filter_kind_init_no_bias,
        test_filter_kinds_mixed_no_bias,
        test_filter_velocity_innovation_wraps,
        test_ekf_jacobian_finite_difference,
        test_ekf_init_round_trip,
        test_ekf_missing_snr_uses_nominal,
        test_ekf_matches_reference,
        test_ekf_nees_straight_100m_20db,
        test_ekf_nees_long_range_300m_13db,
        test_track_api,
        test_a_one_target,
        test_b_crossing_no_swap,
        test_c_false_alarms_only,
        test_d_vanish_deleted_in_time,
        test_e_fixed_vs_jittered_dt,
        test_f_slow_crossing,
        test_g_stop_and_go,
        test_h_second_dot,
        test_i_spurs_synthetic,
        test_i_spurs_reference_sessions,
        test_k_triangle_kinds,
        test_p_braking,
        test_q_passing,
        test_r_coloured_range_noise,
        test_s_spur_handover,
        test_confirm_needs_a_hit,
        test_confirmation_delay,
        test_track_num_confirmation_order,
        test_track_theta_snapshot,
        test_track_birth_t_snapshot,
    ]
    failures = skipped = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Skip as e:
            skipped += 1
            print(f"SKIP  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")

    print(f"\n{len(tests) - failures - skipped}/{len(tests)} passed, {skipped} skipped")
    if failures:
        raise SystemExit(1)
