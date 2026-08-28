# ANTSDR FMCW Radar

FMCW radar at **5.8 GHz** on an ANTSDR E200 (Zynq + AD9361). Two tracks run in parallel:

1. **Python radar** — a reusable `common` DSP/GUI core, an `offline` simulation, and an
   `online` (live SDR) path. Working end to end.
2. **PL fabric offload** — custom HDL in the forked firmware stack moving chirp generation
   and dechirp into the FPGA. Active track (`TODO.md` Part I).

**Terminology:** *offline* = simulation / soft model. *online* = real-time hardware.

**`TODO.md` is the roadmap and the authority on status** — read it before planning work.
As of 2026-08 it tracks **unfinished work only**: closed parts are one line each and their
design conclusions live *here* instead. When a part closes, summarize it there and move
anything worth keeping into this file — don't let narrative grow back in `TODO.md`.
Short version (2026-08): Parts A–F done (offline model, online app, GUI, target sim, DSP
hardening, cable loopback = first real RF), with a handful of small open items still listed.
**Part I (HDL offload) is active** — I1 landed on hardware, I2 (chirp NCO in fabric) has
passed its simulation gate and is at hardware bring-up. Part G (antennas, first radiated
RF) is the next RF step. Part H (PA/LNA/BPF) deferred. Part J (synthetic wideband) blocks
nothing.

## Project structure

```
src/python/                 # import root
  common/
    config.py    # RadarConfig dataclass — ALL parameters (source of truth)
    dsp.py       # pure DSP: generate_chirp(_sequence), estimate_chirp_offset,
                 #   frame_sync_linear/circ, mix_signal, cfar_ca_2d, nms, subbin_refine,
                 #   align_down_doppler, apply_doppler_shift, apply_noise, inst_freq,
                 #   spectrogram, CPIContext, build_cpi_context, process_cpi
    gui.py       # RadarDisplay (PySide6 + pyqtgraph), 3 tabs
  offline/
    soft_model.py        # SoftFMCWModel — target/noise/impairment sim, __main__ entry
    test_soft_model.py   # numeric detector self-test
  online/
    sdr.py         # AntSDR: connect/find_device in __init__; attrs+buffers in start()
    capture.py     # capture_rx_data(): read_block -> TargetSim.apply -> frame_sync_linear
    target_sim.py  # FakeTarget / TargetSim — moving fake targets on ANY input
    processing.py  # process_rx_data(): stateless mix_signal + process_cpi wrapper
    app.py         # RadarWorker (QThread) + main(); owns (config, ctx, sdr)
docs/              # firmware-branch-workflow.md, AntSDR_Phase4_Chirp_NCO_TX_Guide.md,
                   #   fmcw_fabric_architecture.svg, archive/ (Phase 1-3 guides)
hardware/          # materials.md (phased buy list), fmcw_58ghz_block_diagram.svg
firmware/          # submodule stack (see below), branch e200-custom
scripts/bringup/   # hardware bringup references (reference.py, trx_loopback.py, pluto_sdr_ref.py)
vhdl_ls.toml       # rust_hdl LSP config, points at firmware/.../projects/e200
pypkgs.txt         # numpy, matplotlib, pylibiio, scipy, PySide6, pyqtgraph, vunit_hdl
```

`src/python/README.md` is **stale** (describes CaptureThread/ProcessingThread classes that
no longer exist) — trust this file and the code, not that README.

## Running

`src/python/` is the import root. Activate the venv first: `source .venv/bin/activate`.

```bash
cd src/python
python -m offline.soft_model        # run simulation + GUI
python -m common.gui                # GUI with pseudo-data only
python -m online.app                # live SDR app (needs AntSDR at SDR_IP)
python -m online.sdr                # standalone loopback test with matplotlib debug plots
```

## Key classes / functions

- **`RadarConfig`** (`common/config.py`) — dataclass holding every parameter. **Authoritative
  source for parameter values**; this file's table below is a convenience copy, keep it in sync.
  Fields carry `metadata` (`label`/`unit`/`scale`/`group`, optional `readonly`) that the GUI
  config tab auto-generates its form from — adding a tagged field adds a GUI row for free.
  `MAX_RANGE` / `MAX_VELOCITY` are `@property` (recomputed per access; `MAX_VELOCITY` is
  triangle-aware via `T_rep`) — do NOT turn them back into class attributes; RE-CONFIGURE
  builds fresh instances. The parameter banner is `describe()`, called by the entry points,
  never at import. Configs are **immutable by convention**: never mutate a live instance,
  construct a new one and swap the reference. (The one deliberate exception is
  `config.MTI_EN`, written per-CPI by the worker so the live checkbox survives config swaps.)
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
  `dsp.apply_doppler_shift`, and one `dsp.apply_noise` at the end. Expiry resets `t_spawn`, so
  trajectories loop. Known failure mode: at `amp` ≈ 1 a fake target steals the sync lock from
  the leakage.
- **`RadarDisplay`** (`common/gui.py`) — Tab 0 "Radar" (up/down RD maps + detections scatter +
  MTI checkbox), Tab 1 "Signals" (RX/IF spectrograms), Tab 2 "Configuration" (auto-generated
  config form, TX instantaneous-frequency plot, derived-characteristics table, fake-target
  editor, RE-CONFIGURE). `QApplication` is owned by the caller, not the widget.
  Two pyqtgraph/Qt gotchas that cost real time: `setClipToView` + auto-downsampling silently
  hides curves whose data is set **before** the first show; and `QDoubleValidator` does *not*
  hard-reject out-of-range input, so `read_cfg_reg` is the actual gate on config values.

### `process_cpi` return order
`(rd_map_db_up, rd_map_db_down, targets, ranges, velocities)`
- `rd_map_db_down` is `None` in sawtooth (non-triangle) mode.
- `targets` is a list of dicts: `{"r": float, "v": float, "kind": "both"|"up"|"down"}`.

`online/processing.process_rx_data` wraps it and appends `if_signal` as a 6th element (the
GUI spectrogram needs it).

## Parameters (`common/config.py` — source of truth)

| Parameter | Value |
|-----------|-------|
| fc (CHIRP_FC_HZ) | **5.8 GHz** |
| BW (CHIRP_BW_HZ) | 50 MHz |
| T (CHIRP_DUR_S) | 100 µs |
| chirp_reps (CHIRP_REPS) | **128** |
| TRIANGLE_EN / MTI_EN | False / False |
| fs (FS) | 56.6 MHz (AD9361 caps: FS 2.083–61.44 MSPS, rf_bandwidth 0.2–56 MHz) |
| CFAR guard / training / pfa / mask_N | 4 / 5 / 1e-6 / 5 |
| OP_RANGE_FACTOR | 0.15 → MAX_RANGE 2250 m |
| Derived | range res 3.0 m, velocity res 2.02 m/s, MAX_VELOCITY 129 m/s, proc gain 58.6 dB |
| Soft-model only | TX_PWR_DBM 10, TX_GAIN_DB 13, RX_GAIN_DB 14, ISOLATION_DB 40, DC/IQ errors 0 |
| SDR_IP | 192.168.5.10 |
| SDR_TX_GAIN_DB / SDR_TX_GAIN_MAX_DB | −60.0 / −40.0 (AD9361 hardwaregain = attenuation) |
| SDR_RX_GAIN_MODE / SDR_RX_GAIN_DB | manual / 40.0 |
| SDR_RX_MARGIN_PERIODS | 1 |
| SDR_LOOPBACK_EN / SDR_LOOPBACK_NOISE_SNR_DB | True / 1.0 |

Noise figure is a `run_simulation` argument (`a_noise_figure_db`), not a config field.

## Signal processing conventions — do not change these

### IF mixing
`IF = TX * conj(RX)` (`mix_signal`) — positive beat frequency for positive range. Swapping to
`conj(TX) * RX` flips range beats negative and kills all detections through the `pos` mask.

### Doppler sign
`apply_doppler_shift` uses `exp(-j*2π*f_d*t)` (minus sign). A plus sign gives measured
velocity = −v_true. The minus matches the stop-and-hop model where slow-time frequency =
+f_d for an approaching target.

### dtype discipline
numpy silently upcasts complex64 to complex128 (`np.exp` phasors, `randn` noise), so
`apply_doppler_shift` / `apply_noise` cast back to the input dtype. Keep that.

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
Parabolic fit: `δ = 0.5*(y_m - y_p) / (y_m + y_p - 2*y_0)`. Valid only when denominator < 0
(concave-down peak). Applied along each axis independently after CFAR+NMS.
**Accuracy is not resolution** — it runs after CFAR, so it cannot rescue a target that shares a
CFAR cell with clutter. `CHIRP_REPS` owns detection and resolution; `subbin_refine` owns accuracy.

### Leakage / DC / clutter removal (three distinct mechanisms)
1. **Fast-time mean subtraction** (per chirp row, always on) — kills LO self-mixing and ADC DC.
2. **Close-in mask** — `process_cpi` overwrites the first `CFAR_MASK_N` range bins of the RD map
   with the map's **median magnitude** (not zero: a zeroed block distorts the CFAR training
   estimate). Real leakage is smeared over the first few bins by analog group delay and
   multipath, so one bin is not enough. `CFAR_MASK_N=5` was confirmed against Part F cable data.
3. **MTI** (`MTI_EN`, live checkbox) — per-range-bin slow-time mean subtraction, computed on
   unwindowed rows after step 1 and before windowing, both ramps in triangle mode. It is a notch
   exactly at Doppler bin 0; real clutter has spectral width. Upgrade path: 2-pulse canceller
   (`x[k] - x[k-1]`), then an exponential-average clutter map. Revisit against real clutter in
   Part G.
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

## Online app architecture (`online/app.py`) — keep these invariants

- **`RadarWorker(QThread)` owns the `(config, ctx, sdr)` triple**; they are only ever
  touched from `run()` (the worker thread), including `AntSDR` construction. GUI thread
  never touches sdr/ctx, worker never touches widgets.
- Worker → GUI: Qt signals only (`results`, `error`, `reconfigure_done`, `signals` —
  payload `object` for numpy arrays). Cross-thread emits are auto-queued to the GUI thread.
- GUI → worker: `reconfigure_requested` → `pending_cfg` (a fresh `RadarConfig`), swapped in
  between CPIs (new ctx, `radio.config = new`, `close()`/`start()`), with **last-good revert**
  on exception and `reconfigure_done(ok, cfg)` back to the GUI. `fake_targets_changed` →
  `TargetSim.set_targets` and `mti_signal_changed` → `_mti_en` need **no radio restart**.
  No locks: single-reference / bool assignment is atomic under CPython.
- Spectrogram emits are **throttled to ~2 Hz** (`_last_spec_update`) and cover only 2 chirps.
- `AntSDR` lifecycle: connect + `find_device` in `__init__` (once); all attr writes,
  TX waveform, buffers in `start()` — so `close()` + `start()` is a full reconfigure.
  `set_loopback()` works any time after construction and must be set before `start()`
  (the stale-RX flush fails if no signal path exists).
- Cleanup: `radio = None` before `try`, `finally: if radio is not None: set_loopback(False);
  close()`. `stop()` (GUI thread) flips `_running` then `wait(5000)`s — never `terminate()`.
- TX gain is **clamped** in `start()` to `SDR_TX_GAIN_MAX_DB` (warns, does not raise). This is
  the software EIRP guard — see the regulatory section before raising it.
- Known doc/code mismatch: Part F's write-up said the `start()` RX-live check was made relative
  (a fixed 0.01 normalized amplitude is unreachable through 40 dB of pad at low RX gain), but
  `sdr.py` still tests an absolute `>= 0.01`.

## libiio / `pylibiio` API notes (online path)

Verified against the v0.x binding source (vendored copy reviewed 2026-07; the pip package's
`iio.py`). **v0.x API only — libiio v1.x is incompatible (blocks/streams), don't upgrade.**

- `iio.Buffer(dev, samples_count)`: `samples_count` is in device **samples** (one I/Q pair
  = 4 bytes with both int16 channels enabled), not int16 words. Byte size is computed from
  the channels enabled **at creation time** → enabling channels BEFORE creating the buffer
  is mandatory, not style.
- `buf.read()` returns raw interleaved bytes in channel-index order (`voltage0`=I,
  `voltage1`=Q) → deinterleave with `raw[0::2] + 1j*raw[1::2]`. RX normalization is
  `/(2**11 - 1)` (12-bit ADC in an int16 word); TX scales by `2**15 - 1`.
- `buf.write()` silently truncates to the buffer size — assert the returned byte count.
- No explicit buffer destroy; the kernel DMA buffer is freed on GC (`__del__`). To
  recreate (restart), `cancel()` + drop references first, else EBUSY (one buffer/device).
- `find_device()` / `find_channel()` return `None` silently on a bad name — guard.
- Attr values must be **strings** (`str(value)`); errors raise `OSError` with real errno
  (incl. `refill()` timeouts — `ctx.set_timeout(ms)` to adjust).
- `dev.set_kernel_buffers_count(n)` (default 4) trades RX staleness vs dropped data.
- Throughput: RX buffer = `(CHIRP_REPS + margin) * N_chirp * 4 B` = 2.9 MB per CPI at 128 reps.
  GbE moves ~118 MB/s, so expect a few frames per second until the fabric IF path (Part I5).

## Firmware / HDL track

Four nested forks, all on branch `e200-custom` (`docs/firmware-branch-workflow.md` is the
authority — read it before touching submodules):

```
antsdr-fmcw-radar -> firmware/ -> plutosdr-fw/ -> hdl/ (+ linux/, u-boot, buildroot)
```

- A parent repo records only a gitlink SHA. Commit **inside** the repo where files live, then
  bump pointers **bottom-up**. Feature branches (`feat/chirp-nco`, ...) exist in `hdl` only.
- Custom HDL lives in `firmware/plutosdr-fw/hdl/projects/e200/` (`rx_tap.vhd` → becoming
  `fmcw_core.vhd`, `system_bd.tcl`, `Makefile`, `system_constr.xdc`), VUnit testbenches under
  `test/`. AXI-Lite slave at **0x43C10000**; MAGIC bumps on every register-map change
  (`RXT1` → `FMC1`).
- **Do not run `resetGit.sh`** — it strips the MicroPhase patches and the fork checkpoints.
- Target architecture: chirp NCO **and** dechirp in fabric, Ethernet carries IF samples, frame
  sync deleted. `dsp.py` becomes the golden model verifying fabric output sample-for-sample.
  Bypass bits default to today's behavior. Full spec: `docs/AntSDR_Phase4_Chirp_NCO_TX_Guide.md`.
- Cutover to the fabric path waits for Parts F+G on real RF — never debug new RF and new HDL
  at the same time.

## RF reality (AD9361 numbers, verified against the datasheet)

Typical values, Table 1 of the AD9361 datasheet (nearest characterized band is 5.5 GHz):

| Spec | 800 MHz | 2.4 GHz | 5.5 GHz |
|---|---|---|---|
| Max TX output power (typ) | 8 dBm | 7.5 dBm | **6.5 dBm** |
| TX carrier leakage @ 0 dB atten | −50 dBc | −50 dBc | −50 dBc |
| TX carrier leakage @ 40 dB atten | −32 dBc | −32 dBc | **−30 dBc** |
| RX noise figure (max gain) | — | — | 3.8 dB |

- So **~6.5 dBm typ is the realistic 5.8 GHz TX ceiling**, not 7 — and the E200's own balun,
  filter and switch losses sit on top of that. Treat +5…+6.5 dBm as the practical figure.
  (Part F's "~7 dBm" was the optimistic reading.)
- TX power control range is 90 dB in 0.25 dB steps — that IS the variable attenuator, so no trim
  pads are needed on the bench.
- **Attenuating the TX makes leakage relatively worse**: at 40 dB attenuation carrier leakage is
  only −30 dBc, and that lands in the close-in range bins the mask already covers.
- **RF input absolute maximum is +2.5 dBm peak.** With `SDR_TX_GAIN_MAX_DB = -40` (≈ −33.5 dBm
  out) there is huge margin, but that clamp is the only thing standing between a mistyped gain
  and a dead front end on a cable loop. Keep ≥30 dB of pads in any TX→RX cable path.
- FMCW is **100% duty and constant envelope**: p.e.p. = average power, and any PA needs a
  continuous-rated heatsink/dummy load, not a burst rating.

### Measured on the bench (Part F cable loopback)

- MGC window: `SDR_RX_GAIN_DB` is usable across [−90, +60] dB; the defaults are good.
- **Without artificially injected noise, CFAR misbehaves** — the cable-loopback noise floor is
  too low for the training estimator. That is what `SDR_LOOPBACK_NOISE_SNR_DB` is for; it is not
  cosmetic realism.
- AD9361 tracking knobs live on `ad9361-phy`: `calib_mode`, `bb_dc_offset_tracking_en`,
  `quadrature_tracking_en`. Defaults are right. **Quadrature error shows up as a mirrored ghost
  target in the RD map** — recognize it before chasing it as a DSP bug.

## Regulatory constraints (Sweden, PTS) — researched 2026-08

Applies the moment anything is radiated. Cable/attenuator work (Part F) is unregulated.

**5.8 GHz was a good choice: the 50 MHz chirp at fc = 5.8 GHz spans 5.775–5.825 GHz, which sits
inside BOTH license-free regimes.** Two routes, per PTSFS 2025:1 (PTS exemption regulation):

| Route | Band | Ceiling | Requires |
|---|---|---|---|
| SRD, non-specific (§204) | 5.725–5.875 GHz | **25 mW e.i.r.p.** (14 dBm) | nothing |
| Amateur (§203) | 5.65–5.85 GHz | **200 W p.e.p. fed to the antenna** (antenna gain not counted) | HAREC certificate + call sign |

- SRD route, with the planned 19 dBi TX sector: TX port must be **≤ −5 dBm**. Current clamp
  (`SDR_TX_GAIN_MAX_DB = -40` → ≈ −33.5 dBm) leaves ~28 dB of unused headroom; raising the clamp
  to about **−12 dB** puts the port at ≈ −5.5 dBm, i.e. right at the legal EIRP ceiling. That is
  the correct Part G setting, and it is worth 28 dB of link budget for free.
- Amateur route (Swedish HAREC via SSA exam) unlocks the Part H PA legally: a 2 W FPV PA is
  33 dBm, far under the 200 W ceiling. Amateur use is defined as non-commercial "tekniska
  undersökningar", which covers radar experiments; an unattended radar counts as an automatic
  transmitter and must periodically send its call sign. Amateur is **secondary** at 5.65–5.85 GHz
  (radiolocation and military are primary) — no protection, no interference. Homebrew amateur gear
  is exempt from CE/RED; SRD gear is not.
- **Part J ceiling is legal, not technical**: synthetic bandwidth is capped at 150 MHz by the SRD
  band (5.725–5.875) or 200 MHz by the amateur band (5.65–5.85, centre ≈ 5.75 GHz) → ~1.0 m or
  ~0.75 m fine resolution. The 330 MHz plan in `TODO.md` is loopback-only.
- **EMF (SSMFS 2008:18, 10 W/m² public reference level above 2 GHz)**: a 2 W PA into 19 dBi
  = 158 W EIRP → keep people ~1.1 m out of the main beam; into the 30 dBi dish = 2 kW EIRP → ~4 m.
  At 200 W into 19 dBi it is ~11 m. Dummy-load discipline until the antennas point away from people.
- 880–960 MHz (the old 900 MHz carrier) is cellular uplink/downlink — never radiate there.

## Physical range note

Part G's link budget (0 dBm TX, 19 dBi sector + 30 dBi grid, 1 m² target) gives ~−90 dBm at
100 m and ~−118 dBm at 500 m against a ~−91.5 dBm noise floor, which coherent processing gain
turns into ~60 dB / ~35 dB post-processing SNR. **Antennas before PA**: TX power buys range^(1/4),
NF buys SNR linearly. The real ceiling on a monostatic 100%-duty FMCW system is TX/RX isolation,
not the PA and not the law.
