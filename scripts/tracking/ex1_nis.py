import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import chi2

from kf import Target, Radar, CvKalmanFilter, cross_check

# Simulation parameters
SEED = 42
DT, TOTAL_TIME = 0.04, 60.0
SR, SV, SA_TRUE = 0.7, 0.5, 5.0

# NIS of a consistent filter is chi-square with dof = measurement dimension (r, v)
NIS_DOF = 2
NIS_LO, NIS_HI = chi2.ppf([0.025, 0.975], NIS_DOF)  # single-sample 95 % band


# Helpers
def run(sa_filter, seed=SEED):
    """
    One simulation with the filter at sa_filter; truth always at SA_TRUE.
    """
    rng = np.random.default_rng(seed)
    # Initialize the radar, target
    radar = Radar(sr=SR, sv=SV, rng=rng)
    target = Target(r=500.0, v=-3.0, sa=SA_TRUE, rng=rng)
    # Initialize the filter with the first measurement
    kf = CvKalmanFilter(dt=DT, process_noise_std=sa_filter, sr=SR, sv=SV)
    kf.init(radar.measure(target))

    num_steps = round(TOTAL_TIME / DT)

    # Lists to store results
    true_states = []
    measurements = []
    estimates = []
    nis_values = []
    sigmas = []

    # Run the simulation
    for _ in range(num_steps):
        # Update the target state
        target.update(DT)
        true_states.append((target.r, target.v))

        # Get a noisy measurement
        measurement = radar.measure(target)
        measurements.append(measurement)

        # Predict the next state
        kf.predict()

        # Update the filter with the new measurement and compute NIS
        nis = kf.update(measurement)
        nis_values.append(nis)

        # Store the estimate
        estimates.append(kf.x.copy())
        # Store the 1-sigma of r and v from P
        sigmas.append(np.sqrt(np.diag(kf.P)))

    # Convert lists to numpy arrays for easier plotting
    t = np.arange(1, num_steps + 1) * DT  # time of each update [s]
    true_states = np.array(true_states)
    measurements = np.array(measurements)
    estimates = np.array(estimates)
    sigmas = np.array(sigmas)
    nis_values = np.array(nis_values)

    return {
        "t": t,
        "truth": true_states,
        "meas": measurements,
        "est": estimates,
        "sigma": sigmas,
        "nis": nis_values,
    }


def plot(res, title):
    plt.figure(figsize=(12, 11))
    # NIS over time, with the single-sample 95 % band for 2 dof
    plt.subplot(3, 2, 1)
    plt.plot(res["t"], res["nis"], ".", ms=2, label="NIS")
    plt.axhline(
        NIS_HI, color="r", linestyle="--", label=f"95% upper bound ({NIS_HI:.2f})"
    )
    plt.axhline(
        NIS_LO, color="g", linestyle="--", label=f"95% lower bound ({NIS_LO:.2f})"
    )
    plt.title("NIS over time")
    plt.xlabel("Time [s]")
    plt.ylabel("NIS")
    plt.legend()

    # Running mean NIS over the first k updates, with its 95 % band chi2.ppf(., 2k) / k
    k = np.arange(1, len(res["nis"]) + 1)
    running_mean = np.cumsum(res["nis"]) / k
    lo = chi2.ppf(0.025, NIS_DOF * k) / k
    hi = chi2.ppf(0.975, NIS_DOF * k) / k
    plt.subplot(3, 2, 2)
    plt.plot(res["t"], running_mean, label="Running mean NIS")
    plt.fill_between(res["t"], lo, hi, color="gray", alpha=0.3, label="95% band")
    plt.axhline(NIS_DOF, color="k", linestyle=":", label=f"Expected ({NIS_DOF} dof)")
    plt.ylim(0, max(4, 1.1 * running_mean.max()))
    plt.title(f"Running mean NIS (final {running_mean[-1]:.2f})")
    plt.xlabel("Time [s]")
    plt.ylabel("Mean NIS")
    plt.legend()

    # Estimate error with ±2σ from P, for r and v
    errors = res["est"] - res["truth"]
    for i, (name, unit) in enumerate([("Range", "m"), ("Velocity", "m/s")]):
        plt.subplot(3, 2, 3 + i)
        plt.plot(res["t"], errors[:, i], label="Error")
        plt.fill_between(
            res["t"],
            -2 * res["sigma"][:, i],
            2 * res["sigma"][:, i],
            color="gray",
            alpha=0.3,
            label="±2σ",
        )
        plt.axhline(0, color="k", linewidth=0.5)
        plt.title(f"{name} error")
        plt.xlabel("Time [s]")
        plt.ylabel(f"{name} error [{unit}]")
        plt.legend()

    # Range over time: measurements, truth and the track on top, full width
    plt.subplot(3, 1, 3)
    plt.plot(res["t"], res["meas"][:, 0], ".", ms=2, alpha=0.4, label="Measured")
    plt.plot(res["t"], res["truth"][:, 0], "k", label="Truth")
    plt.plot(res["t"], res["est"][:, 0], "r", linewidth=1, label="Track")
    plt.title("Range over time")
    plt.xlabel("Time [s]")
    plt.ylabel("Range [m]")
    plt.legend()

    plt.suptitle(title)
    plt.tight_layout()


if __name__ == "__main__":
    # Hypotheses:
    # 0.5:
    #   - filter is too confident,
    #   - estimate error will be larger than ±2σ,
    #   - NIS will be above the 95% band
    #   - Range error will be slower and drift out of bounds, not fast noise
    #   - RMS will be larger than at 5.0
    # 5.0:
    #   - filter is tuned correctly,
    #   - estimate error will be within ±2σ,
    #   - NIS will be within the 95% band
    # 50.0:
    #   - filter is too conservative and trusts the measurements too much,
    #   - estimate error will be smaller than ±2σ,
    #   - NIS will be below the 95% band
    #   - Range error will be fast and self-correcting, no slow drift
    #   - RMS will be smaller larger than at 5.0 but not as big as 0.5 due to not drifting
    #  Results:
    #     sa  mean NIS  >hi %  <lo %  RMS r [m]  RMS v [m/s]
    #    0.5      4.39   18.4    1.5      0.310        0.735 ✓
    #    5.0      1.96    2.3    2.5      0.120        0.275 ✓
    #   50.0      1.08    0.5    6.9      0.121        0.478 ✓

    try:
        dx, dp, dnis = cross_check(
            DT, SA_TRUE, SR, SV, SA_TRUE, round(TOTAL_TIME / DT), SEED
        )
        print(
            f"FilterPy cross-check, max |diff|: x {dx:.1e}, P {dp:.1e}, NIS {dnis:.1e}"
        )
    except ImportError:
        print("FilterPy not installed, cross-check skipped")

    print(
        f"{'sa':>6} {'mean NIS':>9} {'>hi %':>6} {'<lo %':>6} {'RMS r [m]':>10} {'RMS v [m/s]':>12}"
    )
    for sa_filter in [0.5, 5.0, 50.0]:
        res = run(sa_filter)
        nis = res["nis"]
        rms = np.sqrt(np.mean((res["est"] - res["truth"]) ** 2, axis=0))
        print(
            f"{sa_filter:6.1f} {nis.mean():9.2f} {100 * np.mean(nis > NIS_HI):6.1f}"
            f" {100 * np.mean(nis < NIS_LO):6.1f} {rms[0]:10.3f} {rms[1]:12.3f}"
        )
        plot(res, title=f"filter sa = {sa_filter}, truth sa = {SA_TRUE}")
    print(f"consistent filter: mean NIS {NIS_DOF}, 2.5 % above hi, 2.5 % below lo")
    plt.show()
