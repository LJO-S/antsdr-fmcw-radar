---
paths:
  - "src/python/common/{dsp,test_dsp}.py"
  - "src/python/online/{target_sim,test_target_sim,processing}.py"
  - "src/python/offline/{soft_model,test_soft_model,profile_cpi}.py"
  - "scripts/angle/**"
---

# DSP: conventions and detection pipeline

## Key classes / functions

- **`common/dsp.py`** — pure functions. `process_cpi(if_signal, config, ctx)` is the pipeline;
  `build_cpi_context(config)` precomputes chirp, axes and windows once (`CPIContext`).
- **`SoftFMCWModel`** (`offline/soft_model.py`) — simulation only: `add_delay`/`add_amplitude`
  build the echo, `add_imperfections` injects TX leakage / DC / IQ imbalance,
  `simulate_received_signal` adds thermal noise, `run_simulation` runs the full chain.
- **`TargetSim`** (`online/target_sim.py`) — injects moving fake targets. `apply()` runs on the
  RAW `read_block()` output **before** frame sync, so `estimate_chirp_offset` sees a realistic
  composite. Ungated by `SDR_LOOPBACK_EN` (only the added noise is gated), so it doubles as a
  live test-signal generator on real RF.
  Per target: analytic kinematics from `t_spawn` (clipped to `MAX_RANGE`/`MAX_VELOCITY` so a
  varying frame rate never distorts the trajectory), `tau = round(2*r*FS/c)`, then
  `echo = amp * np.roll(rx_raw, tau)`. **The circular delay is exact, not an approximation** —
  the RX block is an integer number of chirp periods, so `roll` wraps onto identical data. Then
  `dsp.apply_doppler_shift`, and one `dsp.apply_noise` at the end (float32 draws from the
  `TargetSim`'s own Generator, `TargetSim(a_seed=...)` for repeatable tests; not the legacy
  global `np.random`). Expiry resets `t_spawn`, so
  trajectories loop. Known failure mode: at `amp` ≈ 1 a fake target steals the sync lock from
  the leakage.
  Time base: `apply`/`apply_if` take `a_now` (default `time.monotonic()`), one timestamp for
  every target in the block; the worker passes the capture timestamp, replay the recorded one.
  `snapshot()` / `restore()` carry the targets incl. `t_spawn` through `session.json`.

### `process_cpi` return order
`(rd_map_db_up, rd_map_db_down, targets, ranges, velocities)`
- `rd_map_db_down` is `None` in sawtooth (non-triangle) mode.
- `targets` is a list of dicts: `{"r": float, "v": float, "kind": "both"|"up"|"down"}`.

`online/processing.process_rx_data` wraps it and appends `if_signal` as a 6th element (the
GUI spectrogram needs it).

## Signal processing conventions - do not change these

### dtype discipline
numpy silently upcasts complex64 to complex128 (`np.exp` phasors, `randn` noise), so
`apply_doppler_shift` / `apply_noise` cast back to the input dtype. Keep that.
**Never build a full-length `np.exp` phasor**: a Doppler phase splits into a
per-block and a per-sample part (`exp(a+b) = exp(a)·exp(b)`), so `apply_doppler_shift` uses
two short exps and an outer product (1024-sample blocks), and `apply_if` folds beat, Doppler
and TDM phase into one per-row x per-sample outer product. Same numbers to float32 rounding,
~20x faster.

### Radar equation
`P_rx = (P_t * G_t * G_r * λ² * σ) / ((4π)³ * R⁴)` — note λ², not λ. Gains converted from dB
internally: `10**(G_dB/10)`. TX power: `10**((P_dBm-30)/10)`.

### Noise model
Thermal noise: `N = k_b * T0 * fs * F` where F = noise figure (linear), fs = sampling rate.
Complex noise: `noise_std * (randn + j*randn)`, `noise_std = sqrt(N/2)`. The parameter is
`a_noise_figure_db` — not SNR, not noise power.

### Triangle mode
**Half the chirps per RD map.** The waveform interleaves `CHIRP_REPS` up-chirps and `CHIRP_REPS`
down-chirps (`2·CHIRP_REPS` chirps transmitted total). `process_cpi` splits them (`full[0::2]` =
up, `full[1::2]` = down), so each RD map's slow-time (Doppler) FFT spans only `CHIRP_REPS` chirps.
The slow-time sampling interval is therefore `T_rep = 2·CHIRP_DUR_S` (vs `T` in sawtooth), which
**halves the unambiguous velocity span**. `build_cpi_context` already encodes this via `T_rep`.
(Velocity-bin count = `CHIRP_REPS` in both modes.)

**Down-map alignment & pairing.** Down-chirp RD map uses `fft2(conj(down_matrix_iq))` to bring its
negative-slope range beat to positive frequency; `conj` also mirrors slow-time Doppler. Re-align
with `align_down_doppler()` = `np.roll(np.flipud(x), 1, axis=0)` — `flipud` alone is off by one bin
on an even-length fftshifted axis (this off-by-one previously broke pairing). Both ramps are CFAR'd
WITH NMS (one clean cell per target per ramp). Pairing: `both = up & binary_dilation(down,
iterations=PAIR_TOL=2)` — a ±2-bin gate absorbing noise jitter (range–Doppler coupling `k·v` is
sub-bin at these params, so up/down peaks coincide). Do NOT re-NMS `both_mask` against `avg_pwr` —
it drops valid pairings whose up cell isn't the avg_pwr local max. `both` range/velocity refined on
`avg_pwr = (up_pwr + aligned_down_pwr)/2`; up-only/down-only = remainder after excluding
`binary_dilation(both_mask, 2)`. Keep the pairing gate < NMS radius (`nsize`) to avoid
cross-pairing distinct targets.

### Sub-bin interpolation
Parabolic fit on **log power** (Gaussian interpolation):
`δ = 0.5*(l_m - l_p) / (l_m + l_p - 2*l_0)`, `l = ln(power)`. Valid only when denominator < 0
(concave-down peak). Applied along each axis independently after CFAR+NMS. With the Blackman
window it reads within 0.007 bin; on linear power it read up to 0.09 bin low (S-shaped bias).
`δ` is clipped to ±1: at a local max it is within ±0.5 anyway, but triangle "both" is refined
on `avg_pwr`, where the up cell need not be the max (|δ| > 0.5 in 3-6 % of them, so do not
clip at 0.5). `tdm_phasor`'s Doppler fit was already on log magnitude.

**Range-Doppler coupling (sawtooth, not corrected).** A target's Doppler adds to its beat,
so sawtooth range reads `Δr = v·fc·T_EFF/B` off (~1.2 cm per m/s here: 0.2 m at 17.7 m/s,
1.2 m = 0.4 bin at 100 m/s). It is deterministic (a tracker just sees a constant offset),
and triangle cancels it (up and down shift opposite ways). The textbook fix would be
`r -= v·fc·T_EFF/B` in the sawtooth readout. Any range check against truth must include it.
**Accuracy is not resolution** — it runs after CFAR, so it cannot rescue a target that shares a
CFAR cell with clutter. `CHIRP_REPS` owns detection and resolution; `subbin_refine` owns accuracy.

### Leakage / DC / clutter removal (three distinct mechanisms)
1. **Fast-time mean subtraction** (per chirp row, always on) — kills LO self-mixing and ADC DC.
2. **Close-in mask** — `process_cpi` overwrites the first `CFAR_MASK_N` range bins of the RD map
   with the map's **median magnitude** (not zero: a zeroed block distorts the CFAR training
   estimate). Real leakage is smeared over the first few bins by analog group delay and
   multipath, so one bin is not enough. `CFAR_MASK_N=5` was confirmed against Part F cable data
   and held unchanged in the Part G field test.
3. **MTI** (`MTI_EN`, live checkbox) — per-range-bin slow-time mean subtraction, computed on
   unwindowed rows after step 1 and before windowing, both ramps in triangle mode. It is a notch
   exactly at Doppler bin 0; real clutter has spectral width. Upgrade path: 2-pulse canceller
   (`x[k] - x[k-1]`), then an exponential-average clutter map. The Part G field test needed
   no change.
   **The notch width is fixed in BINS, which is why `CHIRP_REPS` is the lever.** At 32 reps a
   1.5 m/s target sits 0.19 bins from DC and loses ~9–10 dB of itself to the mean subtraction;
   at 256 reps it is 1.5 bins out and loses ~0.2 dB. Longer CPIs are cheap — 25.6 ms at 256
   reps, over which a 50 m/s target migrates 1.3 m, under half a range bin.

### Peak-relative dB
RD maps are normalized to a common max (triangle uses one shared reference for up/down); the GUI
draws with fixed `levels=(-80, 0)`. Absolute dB would blow past the window online (FFT processing
gain ≈ +59 dB at 128 reps on ±1-normalized RX).

## Detection pipeline

```
2D FFT → close-in mask → CA-CFAR → NMS → sub-bin interpolation → (triangle up/down pairing) → targets
```

**CA-CFAR noise estimate** = (19x19 box sum - 9x9 guard box sum) / 280 via `dsp.box_sum`:
direct separable `correlate1d` sums in float64, cast back to float32 (replaced a
19x19 `convolve`, ~8x faster, detections identical). Do not swap in `uniform_filter`: its
running sum carries a strong peak's rounding error along the whole row/column (0.4 % noise
error at 145 dB peak/floor, false alarms at ~165 dB) and spreads a NaN to the map's end. Do
not do the subtraction in float32: it cancels next to a strong target (false detections in
its guard ring).

**The Doppler axis (axis 0) is circular, the range axis is not**. `box_sum`
(axis-0 pass), `nms` (`mode=("wrap", "nearest")`), `subbin_refine` rows and the triangle
pairing dilation (`dilate_doppler`) all wrap it, and `wrap_velocity` folds a refined v back
into [-vmax, vmax). With "nearest" edges a target at +-vmax was reported twice (rows
`REPS-1` and 0), and the edge row was copied into its own CFAR window, which raised its
threshold.

## Fake targets on the fabric IF stream

- **`apply_if` conventions** (fake targets on the dechirped stream - software, downstream of
  the DMA, never exercises the fabric): a delay on an IF stream is a *frequency* shift, so each
  target is a beat tone `exp(+2j*pi*f_b*t_fast)` with `f_b = S*2r/c`, `S = BW/T`, fast time
  reset every chirp (`% N`), sign of `f_b` flipped on odd chirps in triangle mode, amplitude
  relative to the block RMS (the fabric IF is Q15 normalized by `2^11-1`, 16x raw RX, so never
  an absolute level), times a Doppler phasor `exp(+2j*pi*f_d*t)` - the **opposite** sign of
  `dsp.apply_doppler_shift`, which is calibrated for the pre-mix RX domain where
  `mix_signal`'s `conj(RX)` flips it; `apply_if` writes into the IF domain and must flip it
  itself (found on hardware: unflipped gave v = -v_true). It is
  built inline as one outer product per target (no `apply_doppler_shift` call; blocks must
  hold whole chirp legs). Fast time runs at `FS_IF` over `N_IF` samples, and
  targets with `|f_b| >= FS_IF/2` are skipped (logged once). Kinematics shared with `apply()`
  via `_kinematics()`.
