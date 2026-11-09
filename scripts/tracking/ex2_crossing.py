"""
L0 exercise 2: two targets crossing in range, tracked with and without the v measurement.
GNN on d2 (gate 9.21 / 6.63); a swap = a track ends nearer the other target.

    python ex2_crossing.py [n_runs] [sa_filter]     # defaults: N_RUNS, SA_FILTER

Results, 200 runs per dv, SA_TRUE 0.5 (swap rate):
       dv           0.25   0.5    1     2     3     4     6     8
    sa_filter 5:
       r and v      0.37  0.24  0.07  0.00  0.00  0.00  0.00  0.00
       r only       0.61  0.79  0.95  0.97  0.82  0.54  0.09  0.01
    sa_filter 0.5 (matched to the truth):
       r and v      0.30  0.04  0.01  0.00  0.00  0.00  0.00  0.00
       r only       0.60  0.79  0.92  0.09  0.00  0.00  0.00  0.00

- r only above 0.5 is track bounce, not a coin flip: on range alone each track takes the
  nearer detection at the crossing, so the tracks keep their range order and repel.
- r and v at sa 5 fits Phi(-dv / (sqrt(2) sv)) (0.36, 0.24, 0.08, 0.002): the chance that
  the two v detections come in the wrong order. At sa 5 the filter follows one detection,
  so one wrong CPI at the crossing is a swap; at sa 0.5 it takes many (0.04 at dv 0.5).
- r only at matched sa holds from dv 2-3; at sa 5 it needs 6-8. So r only must pick a low
  sa (crossings) or a high one (braking, guide 3.3). Measured v gets both at sa 5: that is
  what v buys.
- NMS merges targets under 3 v bins apart (6 m/s sawtooth, 3 triangle or TDM, 1.5
  triangle + TDM), so with v measured, association is never the limit: GNN is enough.
  Without v, triangle/TDM would swap most crossings at 3-4 m/s.
- dv 0.25 and 0.5 are fuzzy: the truths' v random-walk ~0.3 m/s apart by T_CROSS, and NMS
  would merge them anyway.
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import chi2
from scipy.optimize import linear_sum_assignment
import kf

# Simulation parameters
SEED = 42
N_RUNS = 200
DT = 0.04
T_CROSS = 5.0  # time of crossing
T_END = 10.0  # end of simulation
R_CROSS = 500.0  # crossing range
V0 = -3.0  # target velocity
SR, SV = 0.7, 0.5
SA_TRUE, SA_FILTER = 0.5, 5.0
GATE = {True: chi2.ppf(0.99, 2), False: chi2.ppf(0.99, 1)}  # 9.21 / 6.63
BIG = 1e9
DVS = [0.25, 0.5, 1, 2, 3, 4, 6, 8]  # 3 and 6: 3 v bins in triangle/TDM and sawtooth


# Helpers
def make_scenario(dv, seed=SEED):
    """
    2 crossing targets.
    Truth and measurements are created here.
    """
    ss = np.random.SeedSequence(seed).spawn(3)
    # Create targets
    target1 = kf.Target(
        r=R_CROSS - V0 * T_CROSS, v=V0, sa=SA_TRUE, rng=np.random.default_rng(ss[0])
    )
    target2 = kf.Target(
        r=R_CROSS - (V0 + dv) * T_CROSS,
        v=V0 + dv,
        sa=SA_TRUE,
        rng=np.random.default_rng(ss[1]),
    )
    # Create radar
    radar = kf.Radar(sr=SR, sv=SV, rng=np.random.default_rng(ss[2]))

    # Create the scenario: truth and measurements
    num_steps = round(T_END / DT)
    truth = []
    measurements = []

    truth.append(((target1.r, target1.v), (target2.r, target2.v)))
    measurements.append((radar.measure(target1), radar.measure(target2)))
    for _ in range(num_steps - 1):
        target1.update(DT)
        target2.update(DT)
        truth.append(((target1.r, target1.v), (target2.r, target2.v)))
        measurements.append((radar.measure(target1), radar.measure(target2)))
    return np.array(truth), np.array(measurements)


def track(meas, filters):
    """
    Track the two targets with the two filters, using the Hungarian algorithm for association.
    """
    n_steps, n_det, _ = meas.shape
    n_filters = len(filters)
    gate = GATE[filters[0].dim_z == 2]
    est = np.zeros((n_steps, n_filters, 2))

    # init: track i starts on target i step 0
    for i, f in enumerate(filters):
        f.init(meas[0, i])
        est[0, i] = f.x.copy()

    cost = np.full((n_filters, n_det), BIG)

    for t in range(1, n_steps):
        z = meas[t]  # no shuffle needed: linear_sum_assignment ignores the order
        cost.fill(BIG)  # reset cost matrix
        # 1. predict
        for j in range(n_filters):
            filters[j].predict(DT)
        # 2. cost
        for j in range(n_filters):
            for k in range(n_det):
                y, S = filters[j].innovation(z[k])
                dist = kf.d2(y, S)
                if dist < gate:
                    cost[j, k] = dist
        # 3. Assign: rows, cols = linear_sum_assignment(cost) gives index arrays. Zip them to get (j, k) pairs.
        rows, cols = linear_sum_assignment(cost)
        # 4. Update
        for j, k in zip(rows, cols):
            if cost[j, k] < gate:
                filters[j].update(z[k])
        for j in range(n_filters):
            est[t, j] = filters[j].x.copy()

    return est


def swapped(truth, est):
    w = slice(-25, None)  # last 25 steps
    scale = np.array([SR, SV])  # scale the error by the measurement noise

    def error(track_j, truth_i):
        # 1. Compute the error of track_j with respect to truth_i
        return np.linalg.norm(
            (est[w, track_j] - truth[w, truth_i]) / scale, axis=1
        ).mean()

    straight = error(0, 0) + error(1, 1)
    crossed = error(0, 1) + error(1, 0)
    return crossed < straight


def run(sa_filter, seed=SEED, n_runs=N_RUNS):
    """
    Swap rate per dv, with (True) and without (False) the v measurement.
    """
    rates = {True: [], False: []}

    for dv in DVS:
        swaps = {True: 0, False: 0}
        for i in range(n_runs):
            truth, meas = make_scenario(dv=dv, seed=seed + i)
            for variant in [True, False]:
                filters = [
                    kf.CvKalmanFilter(sa=sa_filter, sr=SR, sv=SV, measure_v=variant),
                    kf.CvKalmanFilter(sa=sa_filter, sr=SR, sv=SV, measure_v=variant),
                ]
                est = track(meas, filters)
                swaps[variant] += swapped(truth, est)
        for variant in [True, False]:
            rates[variant].append(swaps[variant] / n_runs)

    return rates


def plot(rates, n_runs, sa_filter):
    labels = {True: "r and v measured", False: "r only"}
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for variant in (True, False):
        p = np.array(rates[variant])
        se = np.sqrt(p * (1 - p) / n_runs)  # binomial standard error
        (line,) = ax.semilogx(DVS, p, "o-", label=labels[variant])
        ax.fill_between(
            DVS, p - 1.96 * se, p + 1.96 * se, color=line.get_color(), alpha=0.2
        )
    ax.axhline(0.5, color="gray", ls=":", label="coin flip")
    ax.set_xlabel("velocity difference dv [m/s]")
    ax.set_ylabel("ID swap rate")
    ax.set_ylim(-0.02, 1.02)
    ax.set_title(f"Crossing targets: sa_filter={sa_filter}, {n_runs} runs, 95 % band")
    ax.set_xticks(DVS)
    ax.set_xticklabels([str(d) for d in DVS])
    ax.legend()
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    return fig


def plot_estimates(sa_filter, seed=SEED, half_window=2.0):
    """
    One run per dv: range vs time around the crossing, truth (solid) and tracks (dashed).
    Track j starts on target j and has its colour, so a dashed line ending on the other
    colour's solid line is a swap. Left: r and v measured. Right: r only.
    """
    t = np.arange(round(T_END / DT)) * DT
    win = (t > T_CROSS - half_window) & (t < T_CROSS + half_window)
    colors = ["C0", "C1"]
    titles = {True: "r and v measured", False: "r only"}

    fig, axes = plt.subplots(
        len(DVS), 2, figsize=(10, 2.2 * len(DVS)), sharex=True, squeeze=False
    )
    for row, dv in enumerate(DVS):
        truth, meas = make_scenario(dv=dv, seed=seed)
        for col, variant in enumerate([True, False]):
            filters = [
                kf.CvKalmanFilter(sa=sa_filter, sr=SR, sv=SV, measure_v=variant)
                for _ in range(2)
            ]
            est = track(meas, filters)
            ax = axes[row, col]
            for j in range(2):
                ax.plot(t[win], truth[win, j, 0], color=colors[j], lw=3, alpha=0.4)
                ax.plot(t[win], est[win, j, 0], color=colors[j], ls="--", lw=1.2)
            ax.axvline(T_CROSS, color="gray", ls=":", lw=0.8)
            ax.set_ylabel(f"dv={dv}\nr [m]")
            ax.set_title(
                f"{titles[variant]}{'  (swapped)' if swapped(truth, est) else ''}",
                fontsize=9,
            )
            ax.grid(True, alpha=0.3)
    axes[-1, 0].set_xlabel("t [s]")
    axes[-1, 1].set_xlabel("t [s]")
    fig.suptitle(f"Truth (solid) vs tracks (dashed), seed {seed}, sa_filter={sa_filter}")
    fig.tight_layout()
    return fig


if __name__ == "__main__":
    import sys
    import time

    # Optional first argument: number of runs, e.g. `python ex2_crossing.py 20` for a quick look
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else N_RUNS
    # Optional second argument: sa_filter, e.g. 0.5 for the filter matched to the truth
    sa_filter = float(sys.argv[2]) if len(sys.argv) > 2 else SA_FILTER

    t0 = time.perf_counter()
    rates = run(sa_filter, n_runs=n_runs)
    print(f"{n_runs} runs per dv in {time.perf_counter() - t0:.1f} s")

    print(f"{'dv':>6} {'r and v':>9} {'r only':>8}")
    for i, dv in enumerate(DVS):
        print(f"{dv:>6} {rates[True][i]:>9.2f} {rates[False][i]:>8.2f}")

    plot(rates, n_runs, sa_filter)
    plot_estimates(sa_filter)
    plt.show()
