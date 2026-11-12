"""
tests/test_tracking.py

L2 tracker tests (Phase 7 guide 6.9). The filter-level tests run against
CvKalmanFilter; the tracker-level tests (a)-(k) run against Tracker (guide 6.6) and
fail with "Tracker not implemented" until it exists. Detections are synthesized from
ground truth with a seeded Generator, never from process_cpi.
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
from common.tracking import CvKalmanFilter

# ===================================================================================
# Assumed tracker API (guide 6.6 / 6.7). Match these to tracking.py in one place.
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
T0 = 1000.0  # absolute time offset: the tracker must use dt, not t
DT_JITTER = (0.020, 0.050)  # s, guide 6.9
PD = 0.9
FA_PER_CPI = 2.0
SPUR_RANGES = (56.0, 168.0, 337.0, 360.0)  # m, guide 6.9 (i)
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
    """Time until the kinematic check decides on |v|: sigma_d = |v| / 2k (guide 6.5)."""
    gap = abs(a_v) / (2 * a_cfg.TRACK_KINEMATIC_SIGMA)
    return optimize.brentq(lambda t: kin_sigma_d(a_cfg, t, a_rate) - gap, 1e-3, 1e3)


def kin_vmin(a_cfg, a_rate):
    """Slowest |v| the kinematic check can decide within its window."""
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
    st = np.asarray(attr(a_track, "filter").state, dtype=float).ravel()
    return {
        "id": attr(a_track, "id"),
        "status": attr(a_track, "status"),
        "r": float(st[0]),
        "v": float(st[1]),
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
    """Ground-truth target alive over [t_on, t_off); kind: up, down, both or mixed."""

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
        if self.merge:  # NMS: the stronger of two targets in one 7x7 window wins
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
# Filter-level tests (CvKalmanFilter)
# ===================================================================================
def test_filter_dwna_nis_consistent():
    """(j) mean NIS under DWNA truth sits in the 95 % chi-square band for 2 dof."""
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
    """(k, filter) a track started from a noiseless up/down/both detection has no k*v bias."""
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
    k, v = coupling(cfg), -60.0  # k*v = 0.7 m: the size of sr
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
    """A detection wrapped across +-MAX_VELOCITY gives a small v innovation."""
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
    """(d) a vanished target's track is gone within TRACK_DELETE_S (+ one CPI)."""
    cfg = RadarConfig()
    delete_s = cfg.TRACK_DELETE_S if hasattr(cfg, "TRACK_DELETE_S") else None
    if delete_s is None:
        raise AssertionError("config field TRACK_DELETE_S missing")
    rng = np.random.default_rng(SEED)
    t_off = 6.0
    scene = Scene(cfg, rng, [const_v(100.0, 8.0, a_t_off=t_off)])
    log = run(cfg, scene, 10.0, rng)
    # Last detection near the target, from the emitted stream
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
    """(e) fixed and jittered dt give the same tracks: IDs, count and accuracy."""
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
    """(f) slow crossing inside one NMS window: no swap, at most one ID lost."""
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
    """(g) MTI on, walker stops: 4 s gap keeps the ID, 6 s gap gives a new one."""
    cfg = RadarConfig()
    for gap, same in ((4.0, True), (6.0, False)):
        rng = np.random.default_rng(SEED)
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
        assert before, f"gap {gap}: never confirmed before the stop"
        assert after, f"gap {gap}: no confirmed track after the stop"
        id_b, id_a = before[-1], after[-1]
        if same:
            assert id_a == id_b, f"gap {gap}: ID {id_b} -> {id_a}"
        else:
            assert id_a != id_b, f"gap {gap}: ID kept ({id_b})"


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
    """(i) fixed-r detections at v = 2, 10, 40 m/s, Pd 1, never confirm."""
    cfg = RadarConfig()
    for r_spur in SPUR_RANGES:
        for v in (2.0, 10.0, 40.0):
            rng = np.random.default_rng(SEED)
            scene = Scene(cfg, rng, [], a_fa=0)
            sr, sv = cfg.TRACK_STD_R, cfg.TRACK_STD_V
            trk = make_tracker(cfg)
            t, bad = T0, []
            while t < T0 + 4.0:
                det = {
                    "r": r_spur + rng.normal(0, sr),
                    "v": v + rng.normal(0, sv),
                    "kind": "up",
                    "snr_db": 30.0,
                }
                for s in trk.update([det] + scene.step(t - T0, False), t, False):
                    sn = snap(s)
                    if sn["status"] == CONFIRMED and abs(sn["r"] - r_spur) < 12.0:
                        bad.append(sn)
                t += rng.uniform(*DT_JITTER)
            assert not bad, f"spur {r_spur} m at {v} m/s confirmed: {bad[0]}"


def test_i_spurs_reference_sessions():
    """(i) recorded sessions 20261102-19*: no confirmed track sits at a spur range for 1 s."""
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
                    # A fake target passing through moves with its v; a spur stays put
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


# ===================================================================================
if __name__ == "__main__":
    tests = [
        test_filter_dwna_nis_consistent,
        test_filter_kind_init_no_bias,
        test_filter_kinds_mixed_no_bias,
        test_filter_velocity_innovation_wraps,
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
        test_confirmation_delay,
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
