# TODO

Roadmap for the FMCW radar. Parts A (offline consolidation) and B (online scaffolding)
are **done**: the SDR is up and running against raw libiio, the offline soft model and
the online model (worker thread + GUI thread) both work, and results flow to the GUI
via Qt signals. Part C (GUI refurbish + runtime reconfiguration) and Part D (moving
fake targets) are **done**. The carrier moves from 0.9 GHz to **5.8 GHz** (cheap
WiFi/FPV hardware). Next up: **Part E - real-world DSP hardening** (software, zero
cost) while the Part F/G hardware kits are ordered and shipping. Parts E-I below
form the road to real RF.

See `src/python/` for the `common` / `offline` / `online` package split.

---

## Part A - Offline consolidation done

Detector self-test, config/property cleanup, frame-sync helper. `config.py` is the
source of truth for parameters. A2 (MTI / slow-time DC removal in `dsp.process_cpi`)
remains a stretch item if live clutter demands it.

## Part B - Online scaffolding done

`online/sdr.py` (libiio `AntSDR` wrapper: cyclic TX buffer, CPI-sized RX buffer,
`start()/read_block()/close()`), `online/capture.py` (frame sync + simulated loopback
delay/velocity/noise), `online/processing.py`, `online/app.py` (`RadarWorker(QThread)`
→ `results` signal -> `gui.RadarDisplay`). Loopback bring-up validated.

---

## Part C - GUI refurbish + runtime reconfiguration done

Third "Configuration" tab: Tx inst-freq plot (2 chirp periods), derived-characteristics
table, and a config form auto-generated from `RadarConfig` field metadata
(label/unit/scale/group/min-max/readonly; untagged fields stay hidden). RE-CONFIGURE
button -> `reconfigure_requested` signal -> `worker.pending_cfg` -> `radio.close()` /
`start()` cycle with last-good revert on failure (`reconfigure_done = Signal(bool, cfg)`
re-enables the button and snaps the panel back via `set_config`). Signals tab: live
Rx/IF spectrograms (`dsp.spectrogram`, throttled worker emit), shared with the offline
model. Gotchas learned: `setClipToView`/auto-downsampling hides curves whose data is
set before first show; `QDoubleValidator` does not hard-reject out-of-range input, so
`read_cfg_reg` is the gate; AD9361 limits FS 2.083-61.44 MSPS, rf_bandwidth 0.2-56 MHz.

**Deferred from C6**: the loopback-disable test (untick `SDR_LOOPBACK_EN` +
RE-CONFIGURE -> `radio.set_loopback(False)`). Do NOT run it until antennas or dummy
loads are wired - disabling digital loopback routes TX to the physical frontend.

---

## Part D - Moving fake targets (loopback target simulator) done

`online/target_sim.py`: `FakeTarget` dataclass (`r0, v0, a0, amp, duration, t_spawn`)
and `TargetSim` with `set_targets(list)` (atomic-reference swap, GUI thread writes /
worker thread reads) and `apply(a_rx_raw, a_config)` run on the RAW `read_block()`
output BEFORE frame sync, so `estimate_chirp_offset` sees the realistic composite
(leakage + echoes + noise). Per target: analytic kinematics from `t_spawn`
(`r = r0 + v0*age + 0.5*a0*age^2`, `v = v0 + a0*age`, clipped to
MAX_RANGE/MAX_VELOCITY so frame rate never distorts the trajectory),
`tau = round(2*r*FS/c)`, `echo = amp * np.roll(rx_raw, tau)` (exact circular delay -
the RX block is an integer number of periods) + `dsp.apply_doppler_shift`; echoes
summed, one `dsp.apply_noise` at the end. Expiry (`age > duration`) resets `t_spawn`,
so targets loop their trajectory instead of disappearing. `capture.py` order:
`read_block()` -> `target_sim.apply()` (loopback only) -> `frame_sync_linear()`; the
TX leakage stays at range 0 and keeps the sync lock.
`SDR_LOOPBACK_DELAY_M`/`_VELOCITY_MPS` retired from `config.py` (per-target now);
`SDR_LOOPBACK_EN` + `SDR_LOOPBACK_NOISE_SNR_DB` kept. GUI: target-editor table
(r0/v0/a/amp/duration, "+"/"-" buttons) emitting `targets_changed = Signal(object)`
-> `worker.set_targets`; purely software-side, no radio restart. Verified: detection
walks outward in the RD map with drifting IF beat line, second target added live
without restart, velocity sign flip under negative accel, and the documented
sync-steal failure mode at amp ~1 (plus low-SNR sync still locks via the 8-period
correlation averaging).

Gotcha learned: numpy silently upcasts complex64 to complex128 (`np.exp` phasors,
`randn` noise) - `apply_doppler_shift`/`apply_noise` now cast back to the input
dtype so captures stay complex64.

Stretch items left: earliest-peak-above-threshold sync lock (instead of argmax),
1/R^2 amplitude realism via `soft_model.add_amplitude`.

---

## Part E - Real-world DSP hardening (software, zero cost, do while parts ship)

Why now: the digital loopback hides exactly the things a real antenna path is made
of. Four gaps that WILL bite on real RF, all fixable before hardware arrives:

1. **Stationary clutter.** Loopback has zero stationary targets; the real world is
   nothing but. Every wall/tree/parked car lands on the v=0 Doppler ridge at its
   own range. Fast-time mean subtraction (current step 2 in `process_cpi`) only
   removes the range-0 leakage DC - it does nothing about clutter at range > 0.
   MTI (A2) is therefore mandatory, not a stretch item.
2. **Doppler resolution at 5.8 GHz.** vel_res = (c/Fc)/(2 * T_cpi). At 900 MHz with
   32 x 100 us chirps that was ~52 m/s - irrelevant. At 5.8 GHz it is **8.1 m/s per
   bin**: pedestrians (~1.5 m/s) and most cars sit within 1 bin of the clutter
   ridge, so MTI would eat them too. CHIRP_REPS must grow to 128-256
   (-> 2.0 / 1.0 m/s per bin). Bonus: processing gain rises from 52.6 to 61.6 dB.
   Why `subbin_refine` does NOT save the day here - accuracy is not resolution:
   the parabola fit localizes an *isolated, already-detected* peak to ~+-0.05 bins
   (which is why velocity estimates look great in loopback with 32 chirps - every
   sim target is isolated), but it runs AFTER CFAR and cannot help a target that
   never becomes a detection. Slow targets fail earlier in the chain, twice:
   (a) they compete in the same CFAR cell with same-range clutter 40-80 dB
   stronger, and (b) the MTI notch width is fixed in BINS, so at 32 reps a
   1.5 m/s target sits 0.19 bins from DC, correlates ~94% with the mean, and mean
   subtraction removes ~9-10 dB of the target itself; at 256 reps the same target
   is 1.5 bins out and loses ~0.2 dB. The fit also biases when clutter residue
   leaks into its 3-point neighborhood. Longer CPI costs little (25.6 ms @ 256
   reps; a 50 m/s target migrates 1.3 m < half a range bin) and compounds with
   the interpolation: +-0.05 of a 1.0 m/s bin = +-0.05 m/s accuracy. Keep
   `subbin_refine` - it owns accuracy; CHIRP_REPS owns detection and resolution.
3. **Leakage is no longer one clean tap.** Real TX->RX coupling has analog group
   delay and multipath spread, so the leakage smears over the first few range bins
   instead of dechirping to a perfect DC term.
4. **Dynamic range.** Echo-to-leakage spans 60-90 dB against a ~12-bit ADC. MGC
   level and the AD9361 tracking loops (BBDC offset, quadrature) become real knobs.

### E1 - MTI: slow-time clutter removal in `dsp.process_cpi`
- [X] Subtract the per-range-bin slow-time mean: on the `[reps x N]` matrix that is
      `mean(axis=0)` (compare: the existing leakage removal is `axis=1`). Placement:
      after step 2 (fast-time leakage removal), before step 3 (windowing) - the mean
      must be computed on unwindowed rows. Apply to BOTH up and down matrices when
      `TRIANGLE_EN`.
- [X] Understand what it is before coding it: mean subtraction across chirps is a
      notch exactly at Doppler bin 0 - the DFT of `x[n] - mean(x)` has bin 0 forced
      to zero, everything else untouched. Consequences to reason through: (a) it
      removes the TX-leakage ridge too (leakage is stationary), partially overlapping
      step 2; (b) a fake target with v=0 disappears - that IS the E4 verification;
      (c) real clutter has spectral *width* (wind-blown trees flicker at fractions
      of a Hz to a few Hz), which a single-bin notch does not fully remove - the
      upgrades in order of effort are a 2-pulse canceller (`x[k] - x[k-1]` along slow
      time, wider notch, 3 dB SNR cost) and an exponential-average clutter map.
      Start with mean subtraction; revisit against real clutter in Part G.
- [X] Config flag `MTI_EN: bool` in `config.py`. Keep `dsp.process_cpi` reading it
      from the config it already receives - offline soft model gets MTI for free.

### E2 - MTI live toggle in GUI
- [X] Checkbox on the Radar tab (next to the plots, not buried in the config form).
      Recommended plumbing: the `targets_changed`/`set_targets` atomic pattern - a
      new signal -> worker method that flips `worker.cfg.MTI_EN` (a Python bool
      write is atomic; worker reads it next CPI). No radio restart, unlike
      RE-CONFIGURE - MTI is pure DSP, same argument as the target editor.

### E3 - 5.8 GHz defaults + CPI resize
- [X] `config.py`: `CHIRP_FC_HZ` default -> `5.8e9`, `CHIRP_REPS` default -> 128
      (try 256 later). Sanity-check the derived-characteristics table in the GUI:
      vel_res 2.02 m/s @ 128 reps, MAX_VELOCITY 129 m/s, range res 3.0 m @ 50 MHz
      BW, MAX_RANGE 2250 m.
- [ ] Check buffer/throughput consequences: P = 5660 samples @ FS 56.6 MSPS;
      RX buffer = (128+1) * 5660 * 4 B = 2.9 MB per CPI (5.8 MB @ 256+1). The GbE
      link moves ~118 MB/s max, so expect a frame rate of a few Hz - fine, but
      verify the GUI stays responsive and the spectrogram throttling still works.
- [ ] AD9361 RX/TX LO range covers 70 MHz - 6 GHz, so 5.8e9 needs no driver change;
      still verify `sdr.start()` accepts it against real hardware in Part F.

### E4 - Split loopback flag from target injection
- [X] `SDR_LOOPBACK_EN` currently gates BOTH the digital-loopback routing AND
      `TargetSim.apply()` in `capture.py`. On a cable/antenna path (Part F/G) digital
      loopback is OFF but fake-target injection must still work - TargetSim injects
      on the raw block, so it rides ANY input, which is exactly what makes it useful
      as a live test-signal generator on real RF. Don't gate
      `target_sim.apply()`; `SDR_LOOPBACK_EN` keeps gating only
      `radio.set_loopback()`. Decide where `apply_noise` belongs (probably only when
      digital loopback is on - real RF brings its own noise).

### E5 - Close-in leakage/clutter handling (after F, informed by real data)
- [X] Optional CFAR mask for the first N range bins (motivated by gap 3 above).
      Make N a config field; verify against the real leakage spread seen in Part F
      before picking a default.

### E6 - Verification (all offline/loopback, no new hardware)
- [X] Fake target v=0 at 500 m: visible with MTI off, gone with MTI on; a moving
      target in the same capture unaffected. Toggle live while running.
- [X] Slow target (v0 = 2-3 m/s) at 128+ reps: cleanly outside the MTI notch.
- [ ] Offline soft model runs with MTI_EN both ways (regression on detector
      self-test).

Stretch (carried over): earliest-peak sync lock, 1/R^2 amplitude realism.

---

## Part F - Cable loopback: first real RF (needs Phase F kit, ~500 SEK)

The cheapest possible "go real" step: TX1A -> 30-40 dB of SMA pads -> RX1A over a
short cable, `set_loopback(False)`. No antennas, nothing radiated, but the FULL
analog chain is exercised: DAC, TX filters/mixer, real LO, RX front end, ADC. Every
gap from the Part E preamble becomes observable here at zero legal/thermal risk.
TargetSim still works (E4) - fake moving targets riding a real RF path.

- [ ] **Order the Phase F kit first** (pads, jumpers, dummy loads, adapters - see
      `hardware/materials.md`). Never run TX into RX without pads: AD9361 TX can
      reach ~7 dBm at 5.8 GHz and the RX front end is happiest well below -10 dBm
      at the port. Start with 40 dB and trim with `SDR_TX_GAIN_DB`.
- [ ] The deferred C6 test, now safe: dummy loads on TX1A/RX1A, untick
      `SDR_LOOPBACK_EN`, RE-CONFIGURE -> `radio.set_loopback(False)` and confirm
      clean teardown/restart both ways.
- [ ] Fix the `start()` RX-live check: `sdr.py` declares RX live at normalized
      amplitude >= 0.01, which a 40 dB-padded path may never reach at low RX gain.
      Make the threshold config- or noise-floor-relative before blaming the cable.
- [ ] Cable bring-up: frame sync must lock on the real signal (leakage = the cable
      path itself, still strongest + earliest). Look at the Rx/IF spectrograms:
      this is the first honest look at real leakage shape, filter group delay, and
      close-in smear (feeds E5's choice of N).
- [ ] MGC sweep: step `SDR_RX_GAIN_DB` across its range; find where the ADC clips
      (leakage-driven) and where the noise floor drowns the fake targets. Note the
      usable window - this is the Part G starting point.
- [ ] Inspect AD9361 tracking knobs via iio attrs (`calib_mode`,
      `bb_dc_offset_tracking_en`, `quadrature_tracking_en` on ad9361-phy): defaults
      are usually right, but know where they live and what the RD map looks like
      with them toggled - quadrature error shows as a mirrored ghost target.
- [ ] Optional: a long cable (or two pads + long RG58 run) as a fixed real "target"
      at a known electrical length - sanity-checks the range axis end to end.

---

## Part G - Antennas, no PA (order Phase G kit in parallel with E/F)

Link budget says the PA can wait: 0 dBm TX + 19 dBi sector + 30 dBi grid on a
1 m^2 target gives ~-90 dBm at RX at 100 m and ~-118 dBm at 500 m, vs a ~-91.5 dBm
noise floor (kTB @ 56.6 MHz + NF ~5 dB). Raw SNR is thus ~0 dB at 100 m / -26 dB at
500 m, and **+61.6 dB coherent processing gain @ 256 reps** turns that into ~60 dB
and ~35 dB post-processing SNR. Even allowing 20-30 dB for RCS pessimism, pointing
loss, and clutter competition, walking-person/car detection at hundreds of meters
is realistic with NO PA. So: antennas first, PA much later.

- [ ] Mount TX sector + RX grid on tripods, >= 1-2 m apart; dummy-load discipline
      until pointed away from people. Keep `SDR_TX_GAIN_DB` low; **EU 5.8 GHz SRD
      budget is ~25 mW (14 dBm) EIRP** - at 19 dBi TX antenna gain that means TX
      port power <= -5 dBm for nominally legal operation. More is Faraday
      cage/amateur-licence territory (5650-5850 MHz secondary, if licensed).
- [ ] First target: a corner reflector or a car at 50-200 m, then a walking person.
      MTI (E1) earns its keep here - this is the first time the v=0 ridge is real.
- [ ] Isolation experiments: spacing, sheet-metal septum, antenna pointing. Measure
      leakage level vs the digital-loopback baseline; re-run the MGC window sweep.
- [ ] Revisit E5 (close-in mask width) and the MTI notch width against real clutter
      (wind-blown vegetation smears around bin 0).
- [ ] Second RX channel (2R2T is wired in the E200 fabric) parked here as a future
      interferometry/angle idea - note only, not a task.

---

## Part H - PA + LNA + BPF + outdoor range (deferred purchases)

Only after Part G shows the actual range limit, and only into a dummy load, cage,
or with an amateur licence - the SVG's EIRP warning stands. Buy nothing here until
G proves the need; the LNA (RX NF) likely pays off before the PA does (TX power
buys range^(1/4), NF buys it linearly in SNR).

- [ ] LNA at the RX antenna (sets system NF, ~+20 dB / NF ~1.3 dB class) + 3-6 dB
      pad between LNA and RX port. Re-run MGC window sweep.
- [ ] BPF 5725-5875 MHz on RX (blocks WiFi/LTE blockers once the LNA raises gain).
- [ ] PA last: FPV 2W class, 100% duty (FMCW!) -> heatsink + fan; bias sequencing
      via Zynq GPIO (Vg before Vd, never enable without load) per the block diagram.
- [ ] TX harmonic LPF (11.6 GHz) before the antenna once the PA is in.

---

## Part I - HDL offload (parked; see AntSDR_Phase2_Custom_HDL_Guide.md)

Deliberately LAST. Two reasons it is not next: (1) never debug new RF and new HDL
simultaneously; (2) it obsoletes far less of `dsp.py` than feared - an on-board
deramp (mix with TX replica in fabric + decimate) replaces only `mix_signal` and
frame sync (a hardware TX/RX trigger makes sync deterministic). `process_cpi`, the
RD/CFAR/GUI chain, and the offline model all survive, and `dsp.py` becomes the
golden model that verifies the fabric output sample-for-sample (the guide's ramp-test
philosophy, applied to deramp).

Entry criteria: Parts F+G proven on real RF AND the GbE-limited frame rate (a few
Hz at 56.6 MSPS raw IQ) is actually the pain point. The payoff is real: deramped IF
needs only ~a few MSPS, so fabric deramp + decimation buys 10-50x frame rate and
opens the door to on-board 2D FFT later. First fabric block should still be the
guide's transparent `rx_tap` ramp test, unchanged.
