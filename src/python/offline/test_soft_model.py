import math
import numpy as np
import common.config as config
from offline.soft_model import SoftFMCWModel


def compare_value(a_actual, a_reference, a_tol, a_string):
    match = math.isclose(a=a_actual, b=a_reference, rel_tol=0.1, abs_tol=a_tol)
    diff_rel = abs(a_actual - a_reference) / (a_reference + 1e-9)
    if not match:
        print(
            f"{a_string} Mismatch! Reference={a_reference} vs Actual={a_actual} <===> %diff={diff_rel}"
        )
        return False
    else:
        print(
            f"{a_string} Pass!! Reference={a_reference} vs Actual={a_actual} <===> %diff={diff_rel}"
        )
    return True


def recover(a_targets_truth, a_cfg, seed=0):
    np.random.seed(seed)  # noise is random
    checker = True
    model = SoftFMCWModel(a_cfg)
    *_, detected, _, _, _, _, _ = model.run_simulation(
        a_targets=a_targets_truth, a_noise_figure_db=5
    )

    # Create tolerances
    T_rep = 2 * a_cfg.CHIRP_DUR_S if a_cfg.TRIANGLE_EN else a_cfg.CHIRP_DUR_S
    vel_res = (config.c / a_cfg.CHIRP_FC_HZ) / (2 * T_rep * a_cfg.CHIRP_REPS)
    range_res = config.c / (2 * a_cfg.CHIRP_BW_HZ)

    # Keep relevant detections and drop the near-DC / zero-range leakage bin
    detected = [
        d
        for d in detected
        if ((not a_cfg.TRIANGLE_EN) or d["kind"] == "both") and d["r"] >= 2 * range_res
    ]
    detected_ref = sorted(a_targets_truth, key=lambda t: t["range"])

    # Gated nearest-neighbour matching in normalised (range, velocity) space.
    # A detection is only claimed if it falls inside GATE bins of the truth target;
    # otherwise the target is a genuine MISS and the detection stays available, so one
    # missing detection cannot cascade misalignment onto every following target.
    GATE = 3.0  # bins (Euclidean, normalised)

    def dist(d, ref):
        return (
            ((d["r"] - ref["range"]) / range_res) ** 2
            + ((d["v"] - ref["velocity"]) / vel_res) ** 2
        ) ** 0.5

    unmatched = list(detected)
    for ref in detected_ref:
        best = min(unmatched, key=lambda d: dist(d, ref), default=None)
        if best is None or dist(best, ref) > GATE:
            print(
                f"MISS! range={ref['range']:.1f} m  velocity={ref['velocity']:.1f} m/s"
            )
            checker = False
            continue
        unmatched.remove(best)
        checker &= compare_value(best["r"], ref["range"], 2 * range_res, "Range   ")
        checker &= compare_value(best["v"], ref["velocity"], 2 * vel_res, "Velocity")
        print()

    # Anything left over is a false alarm
    for d in unmatched:
        print(f"FALSE ALARM! range={d['r']:.1f} m  velocity={d['v']:.1f} m/s")
        checker = False

    return checker


if __name__ == "__main__":
    a_cfg = config.RadarConfig()

    # TARGET GENERATION
    rng = np.random.default_rng(seed=42)
    r_min = 100  # minimum range for first target [m]
    range_res = config.c / (2 * a_cfg.CHIRP_BW_HZ)
    min_spacing = 20 * range_res  # minimum range separation between targets [m]

    targets = []
    r = r_min
    while r < 0.8 * a_cfg.MAX_RANGE:
        v = rng.uniform(-0.5 * a_cfg.MAX_VELOCITY, 0.5 * a_cfg.MAX_VELOCITY)
        targets.append({"range": float(r), "velocity": float(v), "rcs": 10.0})
        r += min_spacing + rng.uniform(0, 2 * min_spacing)

    print(f"Generated {len(targets)} targets:")
    for t in targets:
        print(f"  range={t['range']:.1f} m  velocity={t['velocity']:.2f} m/s")

    # SAWTOOTH
    a_cfg.TRIANGLE_EN = False
    print("\n--- SAWTOOTH ---")
    result_saw = recover(targets, a_cfg)

    # TRIANGLE
    a_cfg.TRIANGLE_EN = True
    print("\n--- TRIANGLE ---")
    result_tri = recover(targets, a_cfg)

    print(f"\nSawtooth: {'PASS' if result_saw else 'FAIL'}")
    print(f"Triangle: {'PASS' if result_tri else 'FAIL'}")
