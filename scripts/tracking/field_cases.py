"""
Pictures for L2 tracker field cases (tests (p), (q), (r), (g), (s), (i) in
src/python/tests/test_tracking.py). Reruns same scenes, shows what pass/fail shares
hide: where an ID is lost, how a setting moves the share.

    python field_cases.py        # no arguments; edit the constants below

Writes one PNG per case into local/ (git-ignored), prints shares. Uses only public
Tracker interface via test helpers. Scene builders repeat the tests' parameters: changed
test needs same change here; printed shares should match the tests'.

Needs the repo venv: source .venv/bin/activate
"""

import dataclasses
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "python"))
import tests.test_tracking as T  # noqa: E402
from common.config import RadarConfig  # noqa: E402

OUT = Path(__file__).resolve().parent / "local"
SEEDS = T.FIELD_SEEDS  # (p), (q), (r), (s)
G_SEEDS = T.G_SEEDS
SPUR_SEEDS = T.SPUR_SEEDS
SA_SWEEP = (3.0, 5.0, 10.0)  # (p), (q)
WANDER_RMS = (0.25, 0.5, 1.0, 2.0, 3.0)  # m, (r), correlation 1 s
SR_SWEEP = (0.7, 1.5)  # m, (r)
SPUR_V = (2.0, 10.0, 40.0)  # m/s, (i)

PASS, FAIL = "#2e7d32", "#c62828"
ID_COLORS = plt.get_cmap("tab10").colors


def cfg_with(**kw):
    return dataclasses.replace(RadarConfig(), **kw)


# ===================================================================================
# Scene runners: seed -> log (same scenes as the tests)
# ===================================================================================
def p_run(a_cfg, a_seed):
    """(p) car at 15 m/s brakes at 8 m/s^2 to 3 m/s."""
    rng = np.random.default_rng(a_seed)
    dur = (15.0 - 3.0) / 8.0
    sgn = rng.choice([-1.0, 1.0])
    t_b = rng.uniform(3.0, 6.0)
    r0 = 100.0 if sgn > 0 else 400.0
    path = T.Path1D(
        r0, [(0, sgn * 15), (t_b, sgn * 15), (t_b + dur, sgn * 3), (1e4, sgn * 3)]
    )
    scene = T.Scene(a_cfg, rng, [T.Target(path)])
    return T.run(a_cfg, scene, t_b + dur + 5.0, rng)


def q_run(a_cfg, a_seed):
    """(q) car at 15 m/s passing 30 m off boresight."""
    rng = np.random.default_rng(a_seed)
    x0, speed = 100.0, 15.0
    path = T.PassPath(30.0, speed, x0 / speed)
    scene = T.Scene(a_cfg, rng, [T.Target(path)])
    return T.run(a_cfg, scene, 2 * x0 / speed, rng)


def car_run(a_cfg, a_seed, a_rms, a_tau=1.0):
    """(r) car at 10 m/s for 60 s with coloured range error. Returns (log, r0, v)."""
    sgn = 1.0 if a_seed % 2 == 0 else -1.0
    r0, v = (100.0 if sgn > 0 else 700.0), sgn * 10.0
    return T._wander_run(a_cfg, a_seed, r0, v, a_rms, a_tau, 60.0), r0, v


def g_run(a_cfg, a_seed, a_gap):
    """(g) walker stops, detections cease for a_gap s, MTI on."""
    rng = np.random.default_rng(a_seed)
    tg, t_cease, t_resume = T.walker_stop_go(a_cfg, a_gap)
    scene = T.Scene(a_cfg, rng, [tg])
    log = T.run(a_cfg, scene, t_resume + 5.0, rng, a_mti=True)
    return log, t_cease, t_resume


T_H = 5.0  # s, (s) hand-over time


def s_run(a_cfg, a_seed, a_v):
    """(s) walker hands track to stronger spur. Returns (log, ID at hand-over,
    spur r, time handed-over track sat on spur after walker clear, s)."""
    rng = np.random.default_rng(a_seed)
    sgn = rng.choice([-1.0, 1.0])
    walker = T.const_v(100.0, sgn * a_v)
    r_s = walker.truth(T_H)[0]
    v_s = sgn * (a_v + rng.uniform(-0.5, 0.5))
    spur = T.Target(T.SpurPath(r_s, v_s), a_t_on=T_H, a_snr_db=35.0, a_pd=1.0)
    scene = T.Scene(a_cfg, rng, [walker, spur], a_merge=True)
    log = T.run(a_cfg, scene, T_H + 8.0, rng)
    cid = T.match(next(s for s in log if s.t >= T_H), 0)
    if cid is None:
        return log, None, r_s, None
    on = [
        s.t
        for s in log
        if s.t >= T_H
        and abs(s.truth[0][0] - r_s) >= T.R_TOL
        and any(x["id"] == cid and abs(x["r"] - r_s) < T.R_TOL for x in s.confirmed())
    ]
    return log, cid, r_s, (max(on) - T_H if on else 0.0)


# ===================================================================================
# Plot helpers
# ===================================================================================
def plot_tracks(a_ax, a_log, a_ref=None, a_i=0):
    """Truth of target a_i (black) and confirmed tracks, one colour per ID. a_ref(t)
    subtracted from r (zoom on error)."""
    ref = a_ref or (lambda t: 0.0)
    pts = [(s.t, s.truth[a_i][0] - ref(s.t)) for s in a_log if s.truth[a_i] is not None]
    a_ax.plot(*zip(*pts), color="k", lw=4, alpha=0.25, label="truth")
    by_id = {}
    for s in a_log:
        for x in s.confirmed():
            by_id.setdefault(x["id"], ([], []))
            by_id[x["id"]][0].append(s.t)
            by_id[x["id"]][1].append(x["r"] - ref(s.t))
    for k, (i, (ts, rs)) in enumerate(sorted(by_id.items())):
        a_ax.plot(ts, rs, ".", ms=3, color=ID_COLORS[k % len(ID_COLORS)], label=f"ID {i}")
    a_ax.set_xlabel("t [s]")
    a_ax.grid(True, alpha=0.3)
    a_ax.legend(fontsize=7, loc="best", ncol=2)
    return len(by_id)


def strip(a_ax, a_rows, a_labels, a_title):
    """Seeds x settings: green = test criterion met, red = failed. a_rows: bool lists."""
    img = np.array(a_rows, dtype=float)
    a_ax.imshow(img, cmap=ListedColormap([FAIL, PASS]), vmin=0, vmax=1, aspect="auto")
    a_ax.set_yticks(range(len(a_labels)), [
        f"{lab}: {100 * np.mean(r):.0f} %" for lab, r in zip(a_labels, a_rows)
    ])
    a_ax.set_xlabel("seed")
    a_ax.set_title(a_title, fontsize=10)


def save(a_fig, a_name):
    OUT.mkdir(exist_ok=True)
    path = OUT / a_name
    a_fig.tight_layout()
    a_fig.savefig(path, dpi=110)
    plt.close(a_fig)
    print(f"  -> {path}")


# ===================================================================================
# Figures
# ===================================================================================
def fig_p_q():
    """(p) braking, (q) passing: lost ID at sa 5 vs kept at sa 10, share per sa."""
    print("(p) braking, (q) passing")
    fig, axes = plt.subplots(2, 3, figsize=(16, 8))
    for row, (name, runner) in enumerate((("(p) braking", p_run), ("(q) passing", q_run))):
        ok = {}
        for sa in SA_SWEEP:
            cfg = cfg_with(TRACK_STD_A=sa)
            ok[sa] = [
                T.kept_id(runner(cfg, s), 0) for s in range(T.SEED, T.SEED + SEEDS)
            ]
            print(f"  {name} sa {sa:g}: {100 * np.mean(ok[sa]):.0f} % kept")
        # seed failing at sa 5, shown at sa 10 and 5 (same detections)
        bad = [i for i, k in enumerate(ok[5.0]) if not k]
        seed = T.SEED + (bad[0] if bad else 0)
        for col, sa in enumerate((10.0, 5.0)):
            ax = axes[row, col]
            n = plot_tracks(ax, runner(cfg_with(TRACK_STD_A=sa), seed))
            ax.set_ylabel("range [m]")
            ax.set_title(f"{name}, seed {seed}, sa {sa:g}: {n} confirmed ID(s)", fontsize=10)
        strip(
            axes[row, 2],
            [ok[sa] for sa in SA_SWEEP],
            [f"sa {sa:g}" for sa in SA_SWEEP],
            f"{name}: one ID kept per seed (test needs 90 %)",
        )
    save(fig, "p_q_braking_passing.png")


def fig_r():
    """(r) coloured range noise: share vs wander size for two sr, plus one run."""
    print("(r) coloured range noise")
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(14, 5))
    for sr in SR_SWEEP:
        cfg = cfg_with(TRACK_STD_R=sr)
        share = []
        for rms in WANDER_RMS:
            ok = [
                T.kept_id(car_run(cfg, s, rms)[0], 0)
                for s in range(T.SEED, T.SEED + SEEDS)
            ]
            share.append(100 * np.mean(ok))
            print(f"  sr {sr} wander {rms} m rms: {share[-1]:.0f} % kept")
        ax_l.plot(WANDER_RMS, share, "o-", label=f"sr {sr} m")
    ax_l.axhline(90, color="gray", ls=":", label="test bar (1 m rms)")
    ax_l.set_xlabel("range wander, rms [m] (1 s correlation)")
    ax_l.set_ylabel(f"seeds with one ID kept [%] (of {SEEDS})")
    ax_l.set_ylim(-3, 103)
    ax_l.set_title("(r) car at 10 m/s, 60 s")
    ax_l.grid(True, alpha=0.3)
    ax_l.legend()
    # one run at 2 m rms, default sr: error vs straight line r0 + v t
    seed = T.SEED
    log, r0, v = car_run(cfg_with(TRACK_STD_R=SR_SWEEP[0]), seed, 2.0)
    n = plot_tracks(ax_r, log, lambda t: r0 + v * t)
    ax_r.set_ylabel("range minus r0 + v t [m]")
    ax_r.set_title(
        f"seed {seed}, 2 m rms, sr {SR_SWEEP[0]}: {n} confirmed ID(s) (black = true wander)",
        fontsize=10,
    )
    save(fig, "r_coloured_range_noise.png")


def fig_g():
    """(g) stop-and-go: per seed, matched track still the first one?"""
    print("(g) stop and go")
    cfg = RadarConfig()
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True)
    colors = ListedColormap(["#bdbdbd", PASS, "#ef6c00"])  # none, same ID, other ID
    for ax, (gap, want_same) in zip(axes, ((4.0, True), (6.0, False))):
        rows, n_ok = [], 0
        for seed in range(T.SEED, T.SEED + G_SEEDS):
            log, t_cease, t_resume = g_run(cfg, seed, gap)
            t_end = log[-1].t
            grid = np.zeros(int(t_end / 0.1) + 1)
            first = None
            for s in log:
                m = T.match(s, 0)
                if m is None:
                    continue
                first = m if first is None else first
                grid[int(s.t / 0.1) :] = 1 if m == first else 2  # held until next match
            for s in log:  # step without match is grey again
                if T.match(s, 0) is None:
                    grid[int(s.t / 0.1)] = 0
            rows.append(grid)
            ids = [m for s in log if s.t > t_resume + 3.0 and (m := T.match(s, 0)) is not None]
            bef = [m for s in log if s.t < t_cease - 0.2 and (m := T.match(s, 0)) is not None]
            n_ok += bool(bef and ids and ((ids[-1] == bef[-1]) == want_same))
        width = max(len(r) for r in rows)
        img = np.zeros((len(rows), width))
        for i, r in enumerate(rows):
            img[i, : len(r)] = r
            img[i, len(r) :] = np.nan
        ax.imshow(img, cmap=colors, vmin=0, vmax=2, aspect="auto",
                  extent=(0, width * 0.1, len(rows), 0), interpolation="nearest")
        ax.axvline(t_cease, color="k", ls="--", lw=1)
        ax.axvline(t_resume, color="k", ls="--", lw=1)
        ax.set_xlabel("t [s]")
        ax.set_title(
            f"gap {gap:g} s: {'ID kept' if want_same else 'new ID'} in {n_ok}/{G_SEEDS} seeds "
            f"(needs 70 %)", fontsize=10)
        print(f"  gap {gap:g}: {n_ok}/{G_SEEDS}")
    axes[0].set_ylabel("seed")
    axes[0].text(0.01, -0.12, "green = first track, orange = a later track, grey = none; dashed = stop, resume",
                 transform=axes[0].transAxes, fontsize=8)
    save(fig, "g_stop_and_go.png")


def fig_s():
    """(s) spur hand-over: how long handed-over track survives, check on vs off."""
    print("(s) spur hand-over")
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    window = RadarConfig().TRACK_KINEMATIC_WINDOW_S
    bins = np.arange(0, 9.01, 0.5)
    for ax, v in zip(axes, (1.5, 2.0)):
        for label, cfg, color in (
            ("kinematic check on", RadarConfig(), PASS),
            ("kinematic check off", cfg_with(TRACK_KINEMATIC_SIGMA=1000.0), FAIL),
        ):
            durs = [
                d for s in range(T.SEED, T.SEED + SEEDS)
                if (d := s_run(cfg, s, v)[3]) is not None
            ]
            ok = np.mean([d <= window for d in durs])
            print(f"  v {v} {label}: {100 * ok:.0f} % within {window:g} s")
            ax.hist(np.minimum(durs, bins[-1] - 0.01), bins=bins, alpha=0.6, color=color,
                    label=f"{label}: {100 * ok:.0f} % pass")
        ax.axvline(window, color="k", ls="--", lw=1)
        ax.set_xlabel("time the handed-over track sat on the spur, after the walker was clear [s]")
        ax.set_title(f"(s) walker {v} m/s (right edge = still there at the end)", fontsize=10)
        ax.legend()
        ax.grid(True, alpha=0.3)
    axes[0].set_ylabel(f"seeds (of {SEEDS})")
    save(fig, "s_spur_handover.png")


def fig_i():
    """(i) spurs: confirmed share of born spur tracks vs design rate."""
    print("(i) spurs")
    cfg = RadarConfig()
    r_spur = 168.0
    fig, ax = plt.subplots(figsize=(8, 5))
    shares, labels = [], []
    for v in SPUR_V:
        born = confirmed = 0
        for seed in range(T.SEED, T.SEED + SPUR_SEEDS):
            rng = np.random.default_rng(seed)
            scene = T.Scene(cfg, rng, [], a_fa=0)
            trk = T.make_tracker(cfg)
            t, ids, top = T.T0, set(), 0
            while t < T.T0 + T.SPUR_T:
                det = {"r": r_spur + rng.normal(0, cfg.TRACK_STD_R),
                       "v": v + rng.normal(0, cfg.TRACK_STD_V), "kind": "up", "snr_db": 30.0}
                for x in trk.update([det] + scene.step(t - T.T0, False), t, False):
                    sn = T.snap(x)
                    top = max(top, sn["id"] + 1)
                    if sn["status"] == T.CONFIRMED and abs(sn["r"] - r_spur) < 12.0:
                        ids.add(sn["id"])
                t += rng.uniform(*T.DT_JITTER)
            born += top
            confirmed += len(ids)
        shares.append(100 * confirmed / born)
        labels.append(f"{v:g} m/s\n{confirmed} of {born}")
        print(f"  spur {v:g} m/s: {confirmed} confirmed of {born} born ({shares[-1]:.2f} %)")
    ax.bar(range(len(SPUR_V)), shares, color="#1565c0")
    ax.axhline(100 * 0.00135, color="k", ls="--", label="Phi(-3) = 0.135 % (guide 6.5)")
    ax.set_xticks(range(len(SPUR_V)), labels)
    ax.set_ylabel("confirmed spur tracks per born track [%]")
    ax.set_title(f"(i) spur at {r_spur:g} m, {T.SPUR_T:g} s x {SPUR_SEEDS} seeds per speed")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    save(fig, "i_spurs.png")


if __name__ == "__main__":
    fig_p_q()
    fig_g()
    fig_s()
    fig_i()
    fig_r()  # slowest, last
    print(f"Figures in {OUT}")
