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
**Part I (HDL offload) is active** - I1, I2 and I4 (fabric NCO + fabric dechirp, MAGIC
FMC3) are proven on hardware in digital loopback (I4 closed 2026-09-10); the app drives the
fabric through `online/fabric_ctl.py` (I4b, 2026-09-13) and fake targets work on the IF
stream (I4c). Next: I5, decimation to IF rate - `docs/AntSDR_Phase5_IF_Decimation_Guide.md`
is the spec. Decided 2026-09-16: operational range 800 m at the steepest chirp (56 MHz /
100 us); one fixed divide-by-8 as own HDL inside `fmcw_core` - CIC by 4 (4 stages, zero DSPs)
then a 23-tap halfband by 2 (6 multiplies per channel) - both restarting their output grid on
the chirp-leg mark, so every chirp parameter stays free and each leg yields `SWEEP_LEN // 8`
IF samples; `OP_RANGE_FACTOR` is replaced by `MAX_RANGE_M` plus a derived effective range.
The Xilinx fir_compiler route was dropped: ~16 DSP48 slices (7020 has 220, 75 used, 3 ours),
an encrypted model the user's Questa FSE cannot run, and no per-chirp phase restart. The
two unused ADI FIR blocks (~50 slices) get reclaimed in a separate build.
Part G (antennas, first radiated RF) is the next RF step. Part H (PA/LNA/BPF) deferred.
Part J (synthetic wideband) blocks nothing.

## Project structure

```
src/python/                 # import root
  common/
    config.py    # RadarConfig dataclass — ALL parameters (source of truth)
    fabric_regs.py # fmcw_core register map (offsets, CTRL bits, MAGIC) + register_image(cfg):
                 #   the ONLY encoder of RadarConfig -> register values (chirp_ftw, _u32)
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
    target_sim.py  # FakeTarget / TargetSim — moving fake targets: apply() on raw RX (roll delay),
                   #   apply_if() on the fabric IF stream (beat-tone synthesis)
    processing.py  # process_rx_data(): stateless mix_signal + process_cpi wrapper
                   #   (skips mix_signal when FABRIC_DECHIRP_EN)
    app.py         # RadarWorker (QThread) + main(); owns (config, ctx, sdr)
    fabric_ctl.py  # FabricCtl (enable/disable/set_calib_delay sequencing) + SshDevmem transport
    test_fabric_ctl.py, test_target_sim.py  # board-free asserts, run as __main__
docs/              # firmware-branch-workflow.md, AntSDR_Phase5_IF_Decimation_Guide.md (I5 spec),
                   #   fmcw_fabric_architecture.svg, datasheets/, archive/ (Phase 1-4 guides)
hardware/          # materials.md (phased buy list), fmcw_58ghz_block_diagram.svg
firmware/          # submodule stack (see below), branch e200-custom
scripts/bringup/   # hardware bringup references (reference.py, trx_loopback.py, pluto_sdr_ref.py)
                   #   + dechirp_verify.py (I4 exit test: sw vs fabric RD map, --sweep delay),
                   #   chirp_count_check.py (CHIRP_COUNT delta/s = PRF meter)
data/shortcut_quick_commands.txt  # rxtap register recipes: NCO debug views, IF mode, safe reset
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
| SDR_TX_GAIN_DB / SDR_TX_GAIN_MAX_DB | −60.0 / **0.0** (AD9361 hardwaregain = attenuation) |
| SDR_RX_GAIN_MODE / SDR_RX_GAIN_DB | manual / 40.0 |
| SDR_RX_MARGIN_PERIODS | 1 |
| SDR_LOOPBACK_EN / SDR_LOOPBACK_NOISE_SNR_DB | True / 1.0 |
| FABRIC_DECHIRP_EN / FABRIC_DECHIRP_DELAY | False / **34** (digital-loopback constant, measured 2026-09-10; real-RF constant TBD in Part G) |

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
- **Fabric bring-up is worker-owned too**: `fabric = FabricCtl(SshDevmem(SDR_IP))` is built in
  `run()` next to `radio`; two closures `fabric_up(cfg)` / `fabric_down()` wrap it. Order at
  every `start()`: `radio.start()` -> `fabric_up` (check_magic, enable, 3 throwaway
  `read_block()`s because the IF_SEL switch re-rolls the cpack pack phase). Before every
  `radio.close()` (normal, reconfigure, last-good revert): `fabric_down()`, best-effort in
  `finally`. **`enable()` can raise after its `write_seq` already ran** (the STATUS check), so
  `fabric_up` must catch, `disable()`, then re-raise - otherwise `fabric_on` stays False on a
  live fabric. No ssh inside the capture loop: enable costs ~2 round trips, only at
  start/reconfigure.
- TX gain is **clamped** in `start()` to `SDR_TX_GAIN_MAX_DB` (warns, does not raise). Since
  2026-09-13 the clamp is **0.0 dB**, i.e. full TX power (~+6.5 dBm at 5.8 GHz) is reachable:
  with physical attenuators on the bench the software ceiling was only getting in the way.
  The consequence is that **the EIRP guard is now the operator and the pads, not the code** -
  read the regulatory section before radiating, and set `SDR_TX_GAIN_DB` by hand.
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
  bump pointers **bottom-up**. Feature branches (`feature/chirp-nco`, `feature/fabric-dechirp`,
  ...) exist in `hdl` only.
- Custom HDL lives in `firmware/plutosdr-fw/hdl/projects/e200/`: `src/fmcw_core.vhd` (top:
  AXI-Lite slave, CDC, muxes) with `src/nco/` (chirp_generator, chirp_dds, dds_lut + the
  generated `dds_init.txt`), `src/mixer/mixer_dechirp.vhd`, `src/delay/delay_line.vhd`,
  `src/arithmetic/complex_mult.vhd`; plus `system_bd.tcl`, `Makefile`, `system_constr.xdc`.
  VUnit testbenches under `test/` (`run.py`, `fmcw_core/`, `mixer_dechirp/`). AXI-Lite slave at
  **0x43C10000**; MAGIC bumps on every register-map change (`RXT1` → `FMC1` → `FMC3`; current
  **`0x464D4333`**, mirrored in `common/fabric_regs.py` and must match `C_MAGIC` in the VHDL).
- **Do not run `resetGit.sh`** — it strips the MicroPhase patches and the fork checkpoints.
- Target architecture: chirp NCO **and** dechirp in fabric, Ethernet carries IF samples, frame
  sync deleted. `dsp.py` becomes the golden model verifying fabric output sample-for-sample.
  Bypass bits default to today's behavior. Full spec: `docs/AntSDR_Phase4_Chirp_NCO_TX_Guide.md`.
- Cutover to the fabric path waits for Parts F+G on real RF — never debug new RF and new HDL
  at the same time.

### fmcw_core register map (FMC3) - hardware-proven, I4 closed 2026-09-10

| Offset | Name | Access | Function |
|---|---|---|---|
| 0x00 | MAGIC | R | `0x464D4333` "FMC3" |
| 0x04 | CTRL | RW | bit0 `ramp_en`, bit1 `nco_en`, bit2 `tx_src` (0=DMA, 1=NCO), bit3 `triangle_en`, bit4 `rx_dbg_mux` (NCO onto RX ch0), bit5 `sync_src` (0=TDD, 1=chirp_start) |
| 0x08 | SCRATCH | RW | |
| 0x0C | COUNT | R | `valid_in` counter |
| 0x10 / 0x14 / 0x18 | FTW_START / FTW_SLOPE / SWEEP_LEN | RW | shadow, latched at the next chirp boundary after COMMIT |
| 0x1C | CHIRP_COUNT | R | chirp periods, gray-crossed. Delta/s = 10000 sawtooth, **5000 triangle** (fires per period, not per leg) |
| 0x20 | COMMIT | W | any write arms shadow -> active |
| 0x24 | DECHIRP_DELAY | RW | shadow, same COMMIT; replica delay in samples, **must be < 128** (`G_MAX_DELAY`, wraps silently above) |
| 0x28 | DECIM_SEL | RW | reserved on FMC3. Phase 5 guide defines it: `0` = passthrough, `1` = decimate by 8 (own CIC + halfband inside the core), **not** commit-latched; lands with MAGIC FMC4 |
| 0x2C | IF_SEL | RW | 1 = dechirped IF onto the capture (ADC) path, 0 = raw passthrough. A register, **not** a CTRL bit |
| 0x30 | - | - | unmapped on purpose (tb probe), reads `0xDEADC0DE` |
| 0x34 | STATUS | R | bit0 = `dac_enable_i0`. 0 means `dac_data_sel != DMA` and the NCO output is being discarded at the DAC mux |

Rules learned on hardware (all of them cost a hang or a wrong map once):

- **`triangle_en` is NOT quasi-static** despite having its own synchronizer: it only takes
  effect on a COMMIT. Write it in the pre-COMMIT CTRL word.
- **`nco_en` with SWEEP_LEN=0 wedges the core ~76 s** (counter never wraps, COMMIT never
  consumed). Always: shadows -> COMMIT -> `nco_en`. Recovery: clear `nco_en`, re-COMMIT.
- **`sync_src=1` while `nco_en=0` hangs every RX capture** (no chirp_start pulses, DMA
  SYNC_TRANSFER_START waits forever). So `nco_en | tx_src | sync_src` land in ONE write, after
  COMMIT. No power cycle needed: write CTRL back to 0.
- **`ramp_en` outranks `if_sel` in the ADC output mux** - never set it in IF mode.
- **IF_SEL goes up LAST and comes down FIRST.** While IF_SEL=1 the ADC stream *is* the mixer
  output and its valid is gated by the NCO: IF_SEL=1 with the NCO off means zero ADC valids,
  and any `refill()` in flight starves (ETIMEDOUT).
- Proven enable order: FTW_START, FTW_SLOPE, SWEEP_LEN, DECHIRP_DELAY -> CTRL (`triangle_en`
  only) -> COMMIT -> CTRL = `nco_en|tx_src|sync_src(|triangle_en)` -> IF_SEL=1 -> read STATUS
  and require bit0. Disable: IF_SEL=0 -> CTRL=0 -> COMMIT. Recipes in
  `data/shortcut_quick_commands.txt`.
- DECHIRP_DELAY can be re-committed while the NCO runs (reloads the delay line at the next
  boundary, ~100 us) - that is how `dechirp_verify.py --sweep` works without touching CTRL.
- **DMA sync is a level, not a pulse.** `axi_dmac` SYNC_TRANSFER_START clears its wait only on
  a beat *accepted* with sync high in the same cycle; cpack emits one 64-bit beat per 2
  samples, so a 1-sample pulse overlapped a beat only by luck of pack phase (re-rolled every
  `start()`, and flipped by the IF_SEL switch). `o_dma_sync` is now held from `new_period`
  until 2 output valids have passed (+1 cycle), counted on `o_adc_valid` so it stays
  rate- and IF_SEL-latency-independent. Caveat for I6: the transfer starts on the first beat
  after the boundary and a beat holds 2 samples, so capture may begin 0 or 1 sample early
  depending on pack phase. Dechirp alignment is unaffected (DECHIRP_DELAY is pre-DMA).
- One sync per `refill()`: the DMA holds off until the first sync-tagged beat, then free-runs
  for the programmed length.
- `mixer_dechirp` computes **IF = delayed_TX * conj(RX)**, the same convention as
  `dsp.mix_signal`, Q15 with saturation; bit-exact vs the Python reference in VUnit. A 1-sample
  artifact at valid-gap edges is pipeline timing, not an RTL bug.
- **Loopback TX->RX digital latency = 34 samples** (`FABRIC_DECHIRP_DELAY`), measured by
  sweeping DECHIRP_DELAY until the leakage peak lands in range bin 0. The real-RF constant
  differs (loopback taps inside the AD9361 chain) and is still to be measured in Part G.

### Stock RX FIR decimator - how it is controlled (investigated 2026-09-10)

`rx_fir_decimator` is ADI's `ad_add_decimation_filter` hierarchy (Xilinx `fir_compiler`,
fixed decimate-by-8, 128-tap `coefile_int.coe`) sitting **upstream** of `fmcw_core` in the RX
chain. Its `active` pin comes from `axi_ad9361/up_adc_gpio_out` bit 0 via `decim_slice`, i.e.
`ADI_REG_GP_CONTROL` bit0 of the ADC core, which the `cf_axi_adc` driver writes when the
**`cf-ad9361-lpc` channel attr `sampling_frequency`** is set: only two values are accepted, the
phy rate (factor 1) and phy rate / 8 (factor 8; `decimation_factors_available = {1, 8}`,
device-tree `adi,axi-decimation-core-available`). `active=0` is a pure `ad_bus_mux`
passthrough of data *and* valid. The driver boots at factor 1 and `sdr.py` never touches the
lpc rate, so **the decimator is bypassed by default** - the I2/I4 "bypass the RX FIR decimator"
step is satisfied by not setting the lpc rate to FS/8. `phy.filter_fir_en` is the AD9361's
internal FIR, unrelated. In fabric mode the NCO advances on `dac_valid_i0` at full rate, so
factor 8 upstream of the core would alias the chirp; `sdr.start()` should write the lpc
`sampling_frequency` = FS explicitly as a guard. For I5 this block is not reusable in place
(wrong side of the mixer) but the pattern is: a second `ad_add_decimation_filter` between
`fmcw_core` and cpack with `active` driven from DECIM_SEL is the zero-new-DSP option.

### Fabric IF mode on the Python side (`FABRIC_DECHIRP_EN`)

- `capture_rx_data` returns the raw `read_block()` with no frame sync (`sync_src=1` makes every
  DMA transfer start on a chirp boundary) and runs `TargetSim.apply_if` on it;
  `process_rx_data` skips `mix_signal`.
- `register_image(cfg)` is the **only encoder** (RadarConfig -> register values): adds
  DECHIRP_DELAY and IF_SEL and sets `tx_src|sync_src` in CTRL when the flag is on; never sets
  `nco_en` or COMMIT; raises if the delay is outside `[0, 128)`. `fabric_ctl.FabricCtl` owns
  **sequencing only**: `enable(image)` = shadows -> CTRL(triangle_en) -> COMMIT -> CTRL|nco_en
  -> IF_SEL last -> separate STATUS round trip (also the settle margin the first refill()
  needs); `disable()` = IF_SEL=0 -> CTRL=0 -> COMMIT; `set_calib_delay()` = DECHIRP_DELAY +
  COMMIT only; `check_magic()`; refuses an image with `ramp_en`. Transport `SshDevmem`: one
  ssh call per sequence (`devmem` at base+offset, `&&`-chained, BatchMode so a missing key
  fails fast instead of hanging the worker on a password prompt; `ssh-copy-id root@<ip>`
  once). The write order is unit-tested through a recording transport
  (`online/test_fabric_ctl.py`). `dechirp_verify.py` uses the same module.
- **`apply_if` conventions** (fake targets on the dechirped stream - software, downstream of
  the DMA, never exercises the fabric): a delay on an IF stream is a *frequency* shift, so each
  target is a beat tone `exp(+2j*pi*f_b*t_fast)` with `f_b = S*2r/c`, `S = BW/T`, fast time
  reset every chirp (`% N`), sign of `f_b` flipped on odd chirps in triangle mode, amplitude
  relative to the block RMS (the fabric IF is Q15 normalized by `2^11-1`, 16x raw RX, so never
  an absolute level), then `dsp.apply_doppler_shift(echo, -v, cfg)` - **negated**, because that
  function's sign is calibrated for the pre-mix RX domain and `mix_signal`'s `conj(RX)` is what
  flips it; `apply_if` writes into the IF domain and must flip it itself (found on hardware
  2026-09-13: unnegated gave v = -v_true). Targets with `|f_b| >= FS/2` are skipped (logged
  once). Kinematics shared with `apply()` via `_kinematics()`.
- Signals tab in fabric mode: the RX spectrogram *is* the IF, so the worker emits `if_spec =
  None` and the GUI hides the IF plot and relabels the remaining one. The Configuration tab's
  TX instantaneous-frequency plot stays: it is derived from the config, and the NCO sweeps the
  same BW/T/FS.
- `sdr.start()` pins the `cf-ad9361-lpc` channel `sampling_frequency` to FS (I4b-2) so the
  stock `rx_fir_decimator` stays at factor 1.

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
- **RF input absolute maximum is +2.5 dBm peak.** With `SDR_TX_GAIN_MAX_DB = 0.0` a mistyped
  gain puts ~+6.5 dBm straight at the RX port, over the absolute max, so **the pads are now the
  only protection**: keep >=30 dB of attenuation in any TX->RX cable path, and check the pad is
  actually in line before raising `SDR_TX_GAIN_DB`.
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

- SRD route, with the planned 19 dBi TX sector: TX port must be **≤ −5 dBm**. The clamp is now
  0.0 dB (full power, ~+6.5 dBm) and therefore enforces nothing - for radiated Part G work set
  `SDR_TX_GAIN_DB` ≈ **−12 dB** by hand, which puts the port at ≈ −5.5 dBm, right at the legal
  EIRP ceiling. If an unattended/long radiated run is ever left running, put the ceiling back
  into `SDR_TX_GAIN_MAX_DB` for that session rather than trusting the field.
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
