# TODO

---------------------------------------------------------------
Ludvig's DO-NOT-FORGET:
- Fix the offline and online tests if they fail
---------------------------------------------------------------


Roadmap for the FMCW radar. Carrier is **5.8 GHz** (cheap WiFi/FPV hardware).

**This file tracks unfinished work only.** Completed parts get one line each and
their design conclusions move to `CLAUDE.md`, which is the maintained map - don't
grow narrative back in here as parts close.

Current state (2026-09-26): Parts A-F and I are done. Part I (HDL offload) is proven
in digital loopback: fabric chirp NCO, dechirp and /8 decimation (MAGIC FMC4), up to
~30 fps in the GUI. **Next: Part G** (antennas, first radiated RF), which also gates
making fabric mode the default. Part H stays deferred behind G. Part J blocks
nothing and can run in parallel.

---

## Done

- **A - offline consolidation**: detector self-test, `config.py` as the source of truth.
- **B - online scaffolding**: `sdr.py`, `capture.py`, `processing.py`, `app.py`.
- **C - GUI + runtime reconfiguration**: generated config tab, RE-CONFIGURE with revert.
- **D - moving fake targets**: `online/target_sim.py`.
- **E - DSP hardening**: MTI + live checkbox, 5.8 GHz at 128 reps, close-in CFAR mask.
- **F - cable loopback, first real RF**: `CFAR_MASK_N`, MGC window, leakage shape.
- **I - HDL offload** (2026-07-27 to 2026-09-26): register bank (I1), chirp NCO (I2),
  I3 skipped (the capture jitter was in the RX/DMA path), fabric dechirp with
  loopback delay 34 (I4), `fabric_ctl.py` + worker wiring (I4b), fake targets on the
  IF stream (I4c), /8 halfband decimation (I5), no frame sync in the fabric path (I6).
  Conclusions in `CLAUDE.md` ("Firmware / HDL track"), guides in `docs/archive/`.

## Open small items

- [ ] MTI beyond mean subtraction: 2-pulse canceller (`x[k] - x[k-1]`, wider notch,
      3 dB SNR cost), then an exponential-average clutter map. Revisit against real
      clutter in Part G.
- [ ] Frame sync (software mode): earliest-peak-above-threshold lock instead of argmax.
- [ ] 1/R^2 amplitude realism via `soft_model.add_amplitude`.
- [ ] Throughput: fabric /8 reaches ~30 CPIs/s in the GUI but drops as detections
      increase - profile `process_cpi` (CFAR / NMS / sub-bin / pairing) against the
      GUI scatter. Software mode (2.9 MB per CPI over ~118 MB/s GbE) is unmeasured.
- [ ] Offline soft model runs with MTI_EN both ways (detector self-test regression).
- [-] I5b - DSP reclaim, deferred until a fabric FFT runs short of DSPs (123/220 used;
      a range + Doppler FFT is ~10-35, and a 2D FFT is bound by corner-turn BRAM, not
      DSPs). Levers: delete the four unused ADI FIRs (44 DSPs; recipe in the archived
      Phase 5 guide, Section 4.2 - easy to miss: `logic_or/Op1 <- dac_valid_i0`, and
      `rm` the e200 `.dtb` after the dtsi edit), or map the halfband fold (48 -> 24;
      options in `CLAUDE.md`). Done when software mode and `dechirp_verify.py --ab`
      still pass.

---

## Part G - Antennas, no PA

The link budget (`CLAUDE.md`, "Physical range note") says the PA can wait: 0 dBm TX
into a 19 dBi sector and a 30 dBi grid gives ~60 / ~35 dB post-processing SNR at
100 / 500 m on a 1 m^2 target. Antennas first.

- [ ] Mount TX sector + RX grid on tripods, >= 1-2 m apart; dummy-load discipline
      until pointed away from people. SRD limit is 25 mW EIRP: with the 19 dBi
      sector that is `SDR_TX_GAIN_DB` ~ -12 dB (TX port <= -5 dBm). Amateur route:
      `CLAUDE.md` regulatory section.
- [ ] First target: a corner reflector or a car at 50-200 m, then a walking person.
      MTI earns its keep here - the first real v=0 ridge.
- [ ] Isolation experiments: spacing, sheet-metal septum, antenna pointing. Measure
      leakage vs the digital-loopback baseline; re-run the MGC window sweep.
- [ ] Revisit the close-in mask width (`CFAR_MASK_N`) and the MTI notch width against
      real clutter (wind-blown vegetation smears around bin 0).
- [ ] Fabric mode on real RF, after the above works in software mode (never debug new
      RF and new HDL at once): confirm `FABRIC_DECHIRP_DELAY` (61 on cable) with
      `dechirp_verify.py --sweep --no-loopback`, then repeat the targets at /8. When
      they match, make fabric mode the default.
- Parked idea, not a task: a second RX channel for angle. The board runs an AD9364
  image (1R1T in the device tree), so it needs a DT/firmware rebuild first; the
  silicon is an AD9361.

---

## Part H - PA + LNA + BPF (deferred purchases)

Only after Part G shows the real range limit, and only into a dummy load, a cage, or
with an amateur licence. The LNA likely pays off before the PA (NF buys SNR
linearly, TX power buys range^(1/4)).

- [ ] LNA at the RX antenna (sets system NF, ~+20 dB / NF ~1.3 dB class) + 3-6 dB
      pad between LNA and RX port. Re-run MGC window sweep.
- [ ] BPF 5725-5875 MHz on RX (blocks WiFi/LTE blockers once the LNA raises gain).
- [ ] PA last: FPV 2W class, 100% duty (FMCW!) -> heatsink + fan; bias sequencing
      via Zynq GPIO (Vg before Vd, never enable without load) per the block diagram.
- [ ] TX harmonic LPF (11.6 GHz) before the antenna once the PA is in.

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

## Appendix - what the fabric makes possible next

The FPGA changes what is affordable per second, never the physics: bandwidth is
Part J's job, NF and link budget Part H's, EIRP the law's.

- **100% duty**: back-to-back CPIs at /8 enable track filters (alpha-beta/Kalman over
  detections), exponential-average clutter maps (the MTI upgrade path), and long
  noncoherent integration for weak targets.
- **Fast deterministic hop sequencing** in fabric: cuts Part J's hop dwell from
  ~20 ms (software retune + resync) toward the LO settle floor, which is what fixes
  its motion smear.
- **FFT + CFAR in fabric**: microsecond detection latency, and detections-only over
  the link (kB/s) makes the E200 a standalone mast sensor.
- **Second RX channel**: 2R2T doubles the raw rate, hopeless over GbE at full rate,
  easy after dechirp + decimation - the entry ticket to the angle idea in Part G.
