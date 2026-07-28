# TODO

Roadmap for the FMCW radar. Parts A (offline consolidation) and B (online scaffolding)
are **done**: the SDR is up and running against raw libiio, the offline soft model and
the online model (worker thread + GUI thread) both work, and results flow to the GUI
via Qt signals. Part C (GUI refurbish + runtime reconfiguration) and Part D (moving
fake targets) are **done**. The carrier moves from 0.9 GHz to **5.8 GHz** (cheap
WiFi/FPV hardware). Part E (real-world DSP hardening) is essentially done. Current
state (2026-07): **Part F is the active hardware phase** (kit ordered/shipping),
and **Part I has been promoted from "parked" to an active growth track** worked
in the digital domain while orders ship - see the rewritten Part I. Part J
(synthetic wideband) remains a forward-looking study that does not block E-I.

See `src/python/` for the `common` / `offline` / `online` package split.

See `firmware` for all things firmware related. In `firmware/plutosdr-fw/hdl/projects/e200` the custom HDL should reside, along with VUnit testbenches.

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

- [X] **Order the Phase F kit first** (pads, jumpers, dummy loads, adapters - see
      `hardware/materials.md`). Never run TX into RX without pads: AD9361 TX can
      reach ~7 dBm at 5.8 GHz and the RX front end is happiest well below -10 dBm
      at the port. Start with 40 dB and trim with `SDR_TX_GAIN_DB`.
- [X] The deferred C6 test, now safe: dummy loads on TX1A/RX1A, untick
      `SDR_LOOPBACK_EN`, RE-CONFIGURE -> `radio.set_loopback(False)` and confirm
      clean teardown/restart both ways.
- [X] Fix the `start()` RX-live check: `sdr.py` declares RX live at normalized
      amplitude >= 0.01, which a 40 dB-padded path may never reach at low RX gain.
      Make the threshold config- or noise-floor-relative before blaming the cable.
- [X] Cable bring-up: frame sync must lock on the real signal (leakage = the cable
      path itself, still strongest + earliest). Look at the Rx/IF spectrograms:
      this is the first honest look at real leakage shape, filter group delay, and
      close-in smear (feeds E5's choice of N).
- [X] MGC sweep: step `SDR_RX_GAIN_DB` across its range; find where the ADC clips
      (leakage-driven) and where the noise floor drowns the fake targets. Note the
      usable window - this is the Part G starting point.
      >> [-90, +60] dB works
      >> The defaults are good.
      >> The N-bin mask default is good.
      >> Note: without artificially injected noise, the detection algorithm (CFAR) doesnt behave well. This is due to low noise levels with cable loopback.

- [X] Inspect AD9361 tracking knobs via iio attrs (`calib_mode`,
      `bb_dc_offset_tracking_en`, `quadrature_tracking_en` on ad9361-phy): defaults
      are usually right, but know where they live and what the RD map looks like
      with them toggled - quadrature error shows as a mirrored ghost target.
- [-] Optional: a long cable (or two pads + long RG58 run) as a fixed real "target"
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

## Part I - HDL offload (growth track, active in parallel with F/G)

Reframed 2026-07-23: no longer parked. The old rule - never debug new RF and new
HDL simultaneously - still stands, but it forbids *switching the radar over* to an
unproven fabric path while the RF is also unproven. It does not forbid *developing*
the fabric path: every step below is verified in the digital domain (simulation,
ramp tests, digital loopback), making it ideal fill work while Phase F/G kit ships.
The cutover to the fabric path still waits for F+G proven on real RF.

**Target architecture (decided 2026-07-23): chirp generation AND dechirp
(down-mixing) live in PL fabric; Ethernet carries IF samples, not raw Rx.**
Consequences: TX is fabric-generated (NCO) instead of the cyclic DMA buffer; the
RX path dechirps + decimates in fabric and ships a few MSPS of IF through the
existing cpack -> DMA -> libiio pipe; frame sync is deleted (TX and dechirp NCO
share deterministic hardware timing); `process_cpi`, RD/CFAR/GUI, and the offline
model all survive; `dsp.py` (`mix_signal` etc.) becomes the golden model verifying
fabric output sample-for-sample. A bypass register keeps the raw-Rx path
selectable so the Python-only radar keeps working throughout.

**Status**: firmware Phase 1 (v0.39 built from source, SD boot) done. Phase 2
sections 1-5 done: `rx_tap.vhd` module-reference block in the RX datapath,
TEST_RAMP verified on hardware (`data/tap_test.iq`). **I1 done 2026-07-27**
(Phase 3 guide worked end to end): hand-written AXI-Lite slave on `rx_tap` at
0x43C10000 (MAGIC "RXT1" / CTRL / SCRATCH / COUNT), VUnit tb, 2-flop + gray
CDC, devmem + device tree + UIO + `src/c/rxtap.c` mmap peek/poke tool; ramp
toggled live from a shell, no rebuild. Gotchas learned: hold rvalid/bvalid
until consumed (pulsing = bus hang); the sdboot env had NO bootargs default,
so the `generic-uio` kernel arg died silently until a default was added to
u-boot's zynq-common.h; master-consume gating fixed CDC bugs found in sim
first. HDL sits on `feature/rx-tap` (hdl repo), host tool on
`investigation/rx_tap` (top repo) - merge to `e200-custom` as the Phase 4
kickoff per `docs/firmware-branch-workflow.md`. Work lives on branches in the
forked submodule stack. Guides: `docs/AntSDR_Phase[1-4]*.md`.

**Phase 4 architecture (decided 2026-07-27)** - see
`docs/AntSDR_Phase4_Chirp_NCO_TX_Guide.md` + `docs/fmcw_fabric_architecture.svg`:
`rx_tap` grows into ONE core module (`fmcw_core`, new MAGIC "FMC1", same
address) with ports on BOTH datapath sides - TX mux between
`tx_fir_interpolator` and the `axi_ad9361` dac ports, RX section at the
existing tap point - so the NCO and the future dechirp replica stay
phase-coherent inside one entity. Frame start: `axi_ad9361_adc_dma` already
has SYNC_TRANSFER_START=1 with its sync pin fed by `tdd_channel_1`, which
idles HIGH (default pol 0b010) - the core MUXes (never ORs) a chirp_start
pulse onto it; leakage frame sync gets deleted at I6, not improved. Waveform
registers (FTW_START/FTW_SLOPE/SWEEP_LEN) use shadow + COMMIT, latched at
chirp boundaries. Host config path: `common/fabric_regs.py` (pure
RadarConfig -> register image + bit-true NCO model, also emits VUnit golden
vectors) + `online/fabric_ctl.py` (ssh -> rxtap). TX->RX digital latency is
constant once TX is fabric-timed; measured in I3, becomes DECHIRP_DELAY in
Phase 5 (BIST loopback and real RF need separate constants).

Ladder (each rung proven before the next; sim-first per the Phase 3 guide):

- [X] I1 - AXI-Lite register bank on `rx_tap` (Phase 3 guide) - done, see
      status above.
- [ ] I2 - chirp NCO in fabric (Phase 4 guide Sections 4-5): 32+32-bit
      second-order phase accumulator + quarter-wave LUT, full-scale 16-bit to
      match the `2^15-1` DMA chirp scaling, advances on the valid strobe (not
      raw l_clk - CHIRP_COUNT delta over 1 s must read PRF 10000). Prove via
      CTRL.rx_dbg_mux onto RX ch0: the GUI spectrogram is the fabric scope.
      Exit: waveform reconfigured live from a shell, sawtooth and triangle
      both visible, VUnit bit-exact vs the numpy fixed-point model.
- [ ] I3 - TX from fabric (Phase 4 guide Section 6): CTRL.tx_src muxes NCO vs
      DMA at the dac data ports (upack rd_en loop untouched). Exit: RD map
      identical to DMA baseline in digital loopback, AND
      `estimate_chirp_offset` returns the SAME constant every block/run -
      record that constant here: latency = ____ samples (loopback path).
- [ ] I4 - dechirp (Phase 5 guide, to be written): complex multiply RX x
      conj(NCO replica delayed by DECHIRP_DELAY) in `fmcw_core`'s RX section;
      verify against `dsp.mix_signal` sample-for-sample in digital loopback
      (golden-model test, no RF needed).
- [ ] I5 - decimation to IF rate (few MSPS): CIC or FIR after the mixer,
      IF_SEL switches cpack onto the IF stream; only now does Ethernet carry
      IF samples. Frame rate win: 10-50x, and 100% observation duty becomes
      reachable (see appendix).
- [ ] I6 - integration: IF-mode in `online/sdr.py`/`capture.py` (skip frame
      sync, skip `mix_signal`), sync_src=1 as default (every DMA transfer
      starts at a chirp boundary), `fabric_ctl.write_regs` behind
      RE-CONFIGURE, bypass bits exposed in the GUI. Cutover gated on Parts
      F+G.

---

## Part J - Synthetic wideband: stepped-frequency stitching (range res beyond 56 MHz)

**Why.** Range resolution = c/2B, and the AD9361 caps B at ~56 MHz -> ~2.7 m hard
floor per capture. The chip cannot sweep 300 MHz in one chirp, but it CAN retune
its LO anywhere in 70 MHz - 6 GHz. So: capture N ordinary CPIs on stepped carriers
`Fc_k = F0 + k*dF` and fuse them coherently into a synthetic bandwidth
`B_syn = (N-1)*dF + B`. Example plan: N=8 hops, dF=40 MHz, B=50 MHz ->
B_syn = 330 MHz -> **0.45 m** resolution. Pure software + sequencing, zero new
hardware. Radiated caveat: the EU SRD band is 5725-5875 MHz, so on air B_syn tops
out at ~150 MHz (-> 1.0 m); digital/cable loopback is unconstrained.

**Entry criteria: none.** Does not depend on Parts F-I; testable in digital
loopback TODAY, but only after J1 (see the gotcha there - without J1 loopback
physically cannot exercise this).

### The math (know this cold before coding)

A dechirped point scatterer at delay tau, hop k:

    b_k(t) = A * exp(j*2*pi*S*tau*t) * exp(-j*2*pi*Fc_k*tau) * exp(j*pi*S*tau^2)

- **Term 1 - beat frequency** -> the coarse range bin. S is the same every hop,
  so a scatterer occupies the SAME bin in all N per-hop range profiles. This is
  what makes stitching per-bin instead of a big joint problem.
- **Term 2 - carrier phase: the payload.** Across hops it advances linearly in
  Fc_k with slope `-2*pi*tau`. Take the complex value of one coarse bin across
  the N hops, `y_k` - it is a sinusoid in k whose frequency is proportional to
  tau. An FFT over k therefore resolves fine range INSIDE the coarse bin. Fine
  resolution = c/(2*B_syn); unambiguous fine-range window = c/(2*dF) = 3.75 m at
  dF=40 MHz. Choosing dF <= B guarantees window >= coarse bin (3.0 m), so no
  ambiguity - this is WHY hops must overlap or at least abut, not just "for
  safety".
- **Term 3 - RVP (residual video phase)**, `pi*S*tau^2` - up to ~17 rad at 500 m,
  so not numerically small, BUT it is hop-independent (same S, same tau), so it
  drops out of cross-hop processing entirely. Ignore for v1; it only matters for
  full spectral-concatenation stitching, which we are not doing.

Two-scale picture: within-hop FFT = coarse range (3 m bins), cross-hop FFT = fine
range (0.45 m inside a 3.75 m window). Structurally identical to range/Doppler
processing - the second axis is just carrier frequency instead of chirp index.

### The enemy: per-hop phase incoherence

AD9361 LO retunes land at ARBITRARY phase, adding an unknown `psi_k` to every
hop - raw `y_k` is garbage without calibration. The fix is mandatory and free:
**the TX leakage is a stationary reference scatterer at fixed tau_L ~ 0, present
in every hop.** Its measured phase in hop k is `-2*pi*Fc_k*tau_L + psi_k + const`;
rotating hop k's ENTIRE profile by the negative of the measured leakage phase
cancels psi_k (and re-references range to the leakage delay - harmless, that is
~range 0 anyway). This calibration is the make-or-break piece of the whole part;
test it deliberately via J1's jitter knob, not incidentally.

Known residual for v2: a per-hop fractional frame-sync offset `eps_k` adds phase
`2*pi*f_b*eps_k` that GROWS with beat frequency. Single-reference rotation fixes
the leakage bin exactly and distant bins only approximately. Symptom: far targets
stitch worse than near ones. Fix later via the hop overlap regions or a second
reference; do not chase it in v1.

### Motion breaks v1 - accept it

tau drifting between hops adds a cross-hop phase slope indistinguishable from
fine range. Coupling: ~`v * T_hop * Fc/dF` = **2.9 m of false fine-range per
1 m/s** at T_hop = 20 ms. Consequences: v1 is STATIONARY TARGETS ONLY, and
**MTI must be OFF for the stitch path** (or take Doppler bin 0 explicitly) -
MTI deletes exactly the v=0 targets being stitched. v2: estimate v from per-hop
Doppler and de-rotate. The real long-term fix is fast deterministic hop
sequencing in fabric (see the FPGA appendix).

### Tasks

- [ ] **J1 - TargetSim carrier-phase realism (prerequisite - the big gotcha).**
      In digital loopback the echo never touches the carrier: `np.roll` delays
      the BASEBAND signal, so hopping Fc_k changes nothing and every hop returns
      byte-identical data - term 2 simply does not exist in the sim today.
      Fix in `apply()`: multiply each echo by `exp(-j*4*pi*Fc_now*r/c)` using the
      EXACT (un-rounded) range and the config's current hop carrier. Keep the
      integer roll for the coarse delay - clean division of labor: the roll owns
      the coarse bin, the explicit phasor owns the fine range (no fractional-delay
      filter needed). Add `SIM_HOP_PHASE_JITTER: bool`: a random phase applied to
      the whole block per retune, emulating LO incoherence - off for debugging
      the stitcher, on to prove the calibration.
- [ ] **J2 - retune-only path in `online/sdr.py`.** Write the TX+RX LO frequency
      attrs WITHOUT the full `close()`/`start()` cycle (a full restart is seconds;
      a retune should be ~ms). Measure actual retune+settle time - it sets T_hop
      and therefore the motion sensitivity above. Check `calib_mode`: LO moves can
      trigger recalibration; if hop time balloons, try manual mode, and note what
      the RD map looks like both ways (ties into the Part F tracking-knob task).
- [ ] **J3 - hop sequencer in the worker.** Config `HOP_N` / `HOP_STEP_HZ`
      (HOP_N = 0 or 1 -> feature disabled, normal operation). Loop: retune ->
      capture CPI -> per-hop frame sync + range FFT -> store the COMPLEX range
      profile (slow-time mean or Doppler bin 0 - NOT magnitude; the phase is the
      data). Frame sync runs per hop; its per-hop quality is exactly the `eps_k`
      residual discussed above.
- [ ] **J4 - stitcher in `dsp.py` as a pure function** `profiles[N x Nbins] ->
      HRR profile`: leakage-phase calibration, optional Hann across the hop index
      (fine-range sidelobe control at slight resolution cost - N=8 raw samples
      have ugly sidelobes, so also zero-pad the cross-hop FFT), per-bin cross-hop
      FFT, assemble. Build it OFFLINE against synthetic `y_k` first (the usual
      soft-model-first workflow) - the golden test needs no radio at all.
- [ ] **J5 - verification ladder.**
      (1) Offline synthetic: two scatterers 1 m apart in one coarse bin ->
      unresolved single-band, resolved stitched (1 m = ~2.2 fine bins at 330 MHz).
      (2) Loopback with J1, jitter off: same result via two fake targets with
      r0 1 m apart (they land in the same roll tap - that is the point; their
      carrier phasors differ).
      (3) Jitter on: stitcher broken without calibration, clean with it.
      (4) Part F cable: REAL LO incoherence and the real leakage as reference -
      the first honest end-to-end test.
      (5) GUI: HRR profile plot (probably Signals tab) - minimal, do last.

---

## Appendix - what the FPGA offload leverages (and what it cannot)

The fabric changes what you can afford to do per second - never the physics.
Concretely, in rough order of value:

- **Observation duty cycle.** Raw IQ at 56.6 MSPS complex int16 is ~226 MB/s
  against a ~118 MB/s GbE link -> today's capture-ship-idle cycle is blind
  between frames. Fabric deramp + decimation ships only the beat bandwidth
  (a few MSPS), i.e. 10-50x frame rate - but the deeper win is **100% duty**:
  back-to-back CPIs enable track filters (alpha-beta/Kalman over detections),
  exponential-average clutter maps (E1's stated upgrade path), and long
  noncoherent integration for weak targets. All currently impossible, not just
  slow.
- **Determinism.** A hardware TX/RX trigger makes the chirp offset a known
  constant: `estimate_chirp_offset` and the frame-sync machinery are DELETED,
  not accelerated, and the sync-steal failure class dies with them. Deterministic
  fast sequencing is also what rescues Part J from motion smear: hop dwell drops
  from ~20 ms (software retune + resync) toward the LO settle floor.
- **Latency.** Microsecond-class detection once 2D FFT + CFAR live in fabric.
  Only matters when closing a loop (pointing something, triggering a camera) -
  irrelevant for a scope-style display.
- **Standalone operation.** Detections-only over the link is kB/s: the E200
  becomes a mast-mountable sensor with WiFi backhaul, Ethernet tether optional.
- **Second RX channel.** 2R2T doubles the raw rate (hopeless over GbE), but is
  trivial post-deramp - fabric is the entry ticket to the interferometry/angle
  idea parked in Part G.

What it cannot touch: bandwidth and range resolution (that is Part J's job),
noise figure and link budget (Part H), and the EIRP limit (the law). The Part I
entry criteria stand: real RF proven first - never debug new RF and new HDL at
the same time.
