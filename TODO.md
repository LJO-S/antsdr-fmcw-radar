# TODO

Roadmap for the FMCW radar. Carrier is **5.8 GHz** (cheap WiFi/FPV hardware).

**This file tracks unfinished work only.** Completed parts get one line each and
their design conclusions move to `CLAUDE.md`, which is the maintained map - don't
grow narrative back in here as parts close.

Current state (2026-08): **Part I is the active track** (HDL offload; I2's
simulation gate passed, hardware bring-up is next). Part G (antennas) is the next
RF step. Part H (PA/LNA/BPF) stays deferred behind G. Part J (synthetic wideband)
blocks nothing and is testable in loopback today.

See `src/python/` for the `common` / `offline` / `online` package split, and
`firmware/plutosdr-fw/hdl/projects/e200` for the custom HDL + VUnit testbenches.

---

## Parts A-F - done

- **A - offline consolidation.** Detector self-test, config/property cleanup,
  frame-sync helper; `config.py` became the parameter source of truth.
- **B - online scaffolding.** `online/sdr.py`, `capture.py`, `processing.py`,
  `app.py` - the live SDR path end to end.
- **C - GUI refurbish + runtime reconfiguration.** Config tab auto-generated from
  `RadarConfig` field metadata, RE-CONFIGURE cycle with last-good revert, live
  Rx/IF spectrograms.
- **D - moving fake targets.** `online/target_sim.py`, injected on raw
  `read_block()` output before frame sync.
- **E - real-world DSP hardening.** MTI (E1) + live checkbox (E2), 5.8 GHz at
  128 reps (E3), loopback-flag split (E4), close-in CFAR mask (E5), verified on
  the bench (E6). Three items still open below.
- **F - cable loopback, first real RF.** TX -> 30-40 dB of pads -> RX with
  `set_loopback(False)`: confirmed `CFAR_MASK_N`, exercised the MGC window, and
  gave the first honest look at leakage shape and close-in smear. One item
  skipped below.

### Still open from A-F

- [ ] MTI beyond mean subtraction: 2-pulse canceller (`x[k] - x[k-1]`, wider
      notch, 3 dB SNR cost), then an exponential-average clutter map. Revisit
      against real clutter in Part G.
- [ ] Frame sync: earliest-peak-above-threshold lock instead of argmax.
- [ ] 1/R^2 amplitude realism via `soft_model.add_amplitude`.
- [ ] Buffer/throughput check: RX buffer = (128+1) * 5660 * 4 B = 2.9 MB per CPI
      (5.8 MB at 256+1) against a ~118 MB/s GbE link. Expect a few frames per
      second - verify the GUI stays responsive and spectrogram throttling holds.
- [ ] Verify `sdr.start()` accepts 5.8e9 against real hardware (AD9361 LO covers
      70 MHz - 6 GHz, so no driver change is expected).
- [ ] Offline soft model runs with MTI_EN both ways (detector self-test
      regression).
- [-] Skipped: long RG58 run as a fixed real "target" at known electrical length
      to sanity-check the range axis end to end.

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

      **I2 HW bring-up** (sim gate PASSED 2026-08-28: NCO bit-exact vs the
      fixed-point model, sawtooth + triangle; wiring + CDC constraints
      reviewed). Blocker first - the I1 Zynq plumbing never reached
      `e200-custom`, so building as-is boots WITHOUT /dev/uio0:
      - [ ] cherry-pick linux `303cd58a34af` (UIO node @0x43c10000, 0x1000
            window - covers the whole FMC1 map) + u-boot `d6544e4c984`
            (bootargs `uio_pdrv_genirq.of_id=generic-uio`, on feature/rx-tap)
            onto their `e200-custom`; bump gitlinks bottom-up
      - [ ] merge `67435e5` (investigation/rx_tap -> master) for `src/c/rxtap`
            - maps 4 KB word-indexed, covers 0x00-0x34 unchanged
      - [X] add `i_dac_enable_0` -> STATUS @0x34 before burning a build (guide
            S1: `dac_data_sel != 2` silently discards NCO output, invisible
            without it; 0x30 is the tb's unmapped probe, don't collide)
      - [ ] build the FULL image, not just the .bit (u-boot + DTB changed)
      - [ ] synth log: no "No valid object" criticals (a false path matching
            nothing is dropped silently); BRAM inferred for `dds_lut_inst`
            (else the G_DDS_INIT_FILE string generic never reached the module
            reference)
      - [ ] on target: `rxtap 0` = 0x464D4331; RX FIR decimator bypassed (else
            i_adc_valid runs at 1/8 the NCO strobe -> aliased chirp)
      - [ ] ORDER: FTW_START/SLOPE/SWEEP_LEN -> COMMIT -> nco_en. nco_en first
            with SWEEP_LEN=0 wedges the core ~76 s (counter never wraps, COMMIT
            never consumed); recovery = clear nco_en, re-COMMIT.
      - [ ] CHIRP_COUNT delta/s = 10000 sawtooth, 5000 triangle (fires per
            period, not per leg - the guide's "5000 = wrong" is sawtooth-only)
      - [ ] GUI Signals tab: 100 us sawtooth -25 -> +25 MHz; flip CTRL bit3
            then write COMMIT for triangle. RD map is
            nonsense here. sync_src stays 0 all of I2.
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
