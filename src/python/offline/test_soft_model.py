import math
import numpy as np
import common.config as config
import common.dsp as dsp
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


def match_detections(a_detections, a_targets_truth, a_cfg):
    checker = True

    # Create tolerances
    T_rep = 2 * a_cfg.CHIRP_DUR_S if a_cfg.TRIANGLE_EN else a_cfg.CHIRP_DUR_S
    vel_res = (config.c / a_cfg.CHIRP_FC_HZ) / (2 * T_rep * a_cfg.CHIRP_REPS)
    range_res = config.c / (2 * a_cfg.CHIRP_BW_HZ)

    # Keep relevant detections and drop the near-DC / zero-range leakage bin
    detected = [
        d
        for d in a_detections
        if ((not a_cfg.TRIANGLE_EN) or d["kind"] == "both") and d["r"] >= 2 * range_res
    ]
    detected_ref = sorted(a_targets_truth, key=lambda t: t["range"])

    # Gated nearest-neighbour matching in normalised (range, velocity) space.
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


def recover(a_targets_truth, a_cfg, seed=0):
    np.random.seed(seed)
    checker = True
    model = SoftFMCWModel(a_cfg)
    *_, detections, _, _, _, _, _ = model.run_simulation(
        a_targets=a_targets_truth, a_noise_figure_db=5
    )

    checker = match_detections(
        a_detections=detections, a_targets_truth=a_targets_truth, a_cfg=a_cfg
    )

    return checker


def recover_with_offset(a_targets_truth, a_cfg, seed=0, n_offsets=5):
    """
    Frame-sync self-test
    """
    np.random.seed(seed)
    rng = np.random.default_rng(seed=42)
    model = SoftFMCWModel(a_cfg)
    ctx = dsp.build_cpi_context(a_config=a_cfg)
    checker = True

    tx = dsp.generate_chirp_sequence(a_cfg)
    rx = model.simulate_received_signal(
        a_tx_signal=tx, a_targets=a_targets_truth, a_noise_figure_db=5
    )
    rx = model.add_imperfections(a_tx_signal=tx, a_rx_signal=rx)
    P = len(ctx.tx_chirp)
    for n in range(n_offsets):
        offset_truth = rng.integers(0, P)
        rx_rolled = np.roll(rx, offset_truth)
        # Offset recovery
        offset_meas = dsp.estimate_chirp_offset(
            a_rx=rx_rolled, a_ref_period=ctx.tx_chirp
        )
        # +/-1 sample tolerance
        assert (offset_meas - offset_truth) % P in (
            0,
            1,
            P - 1,
        )

        rx_aligned = dsp.frame_sync(a_rx=rx_rolled, a_config=a_cfg, a_ctx=ctx)

        # import matplotlib.pyplot as plt

        # if n == 0:

        #     def inst_freq(x):
        #         # derivative of unwrapped phase -> Hz
        #         return np.diff(np.unwrap(np.angle(x))) * a_cfg.FS / (2 * np.pi)

        #     win = 2 * len(ctx.tx_chirp)  # show ~2 chirp periods
        #     fig, (ax1, ax2, ax3) = plt.subplots(3, 1, sharex=True, sharey=True)

        #     ax1.plot(
        #         inst_freq(ctx_seq := dsp.generate_chirp_sequence(a_cfg))[:win], lw=0.6
        #     )
        #     ax1.set_title("TX reference (boundary @ 0)")
        #     ax1.axvline(0, color="g", ls="--")

        #     ax2.plot(inst_freq(rx_rolled)[:win], lw=0.6)
        #     ax2.set_title(f"misaligned RX (true boundary @ {offset_truth})")
        #     ax2.axvline(offset_truth, color="r", ls="--")

        #     ax3.plot(inst_freq(rx_aligned)[:win], lw=0.6)
        #     ax3.set_title(f"aligned RX (recovered offset {offset_meas})")
        #     ax3.axvline(0, color="g", ls="--")

        #     ax3.set_xlabel("sample")
        #     fig.supylabel("instantaneous freq [Hz]")
        #     plt.tight_layout()
        #     plt.show()

        if_signal = dsp.mix_signal(a_rx_signal=rx_aligned, a_tx_signal=tx)
        _, _, detections, _, _ = dsp.process_cpi(
            a_if_signal=if_signal, a_config=a_cfg, a_ctx=ctx
        )

        checker &= match_detections(
            a_detections=detections, a_targets_truth=a_targets_truth, a_cfg=a_cfg
        )
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

    # FRAME SYNC (random RX start offset recovered before reshape)
    a_cfg.TRIANGLE_EN = False
    print("\n--- FRAME SYNC: SAWTOOTH ---")
    result_sync_saw = recover_with_offset(targets, a_cfg)

    a_cfg.TRIANGLE_EN = True
    print("\n--- FRAME SYNC: TRIANGLE ---")
    result_sync_tri = recover_with_offset(targets, a_cfg)

    print(f"\nFrame sync (sawtooth): {'PASS' if result_sync_saw else 'FAIL'}")
    print(f"Frame sync (triangle): {'PASS' if result_sync_tri else 'FAIL'}")
