
# TODO

---------------------------------------------------------------
Ludvig's DO-NOT-FORGET:
- Range IF filter
- 0 Angle with splitter
- Angle tracker with fake targets

---------------------------------------------------------------

# Order
1. Start on angle estimation:
2. SMA Tee?
3. Fix 3D-case and order the case parts + JPH shit

# Roadmap

Roadmap for the FMCW radar.

**This file tracks unfinished work only.** Completed parts get one line each and
their design conclusions move to `.claude/rules/` (area rules) or `CLAUDE.md`
(project-wide), per the close-out rule in `CLAUDE.md` - don't grow narrative back in
here as parts close.

Current state (2026-11-03): Parts A-G and I are done. **Part K (Phase 6) is active**:
K0-K2, K4, K5, K7, K8 and K10 done. FMC5 (TDM + `tx_sel` on J30) is on hardware, and fabric
/8 has run on cable RF (K7 bench, K10 cable test) but not yet on air (K6). K9 boards
ordered (due ~2026-11-06..12). Next: K3 (tracker) while they ship; K6 waits on weather.
Part H stays deferred. Part J blocks nothing and can run in parallel.

---

## Done

- **A - offline consolidation**: detector self-test, `config.py` as the source of truth.
- **B - online scaffolding**: `sdr.py`, `capture.py`, `processing.py`, `app.py`.
- **C - GUI + runtime reconfiguration**: generated config tab, RE-CONFIGURE with revert.
- **D - moving fake targets**: `online/target_sim.py`.
- **E - DSP hardening**: MTI + live checkbox, 5.8 GHz at 128 reps, close-in CFAR mask.
- **F - cable loopback, first real RF**: `CFAR_MASK_N`, MGC window, leakage shape.
- **G - antennas, no PA** (2026-08, software mode): TX sector + RX grid ~1.5 m apart;
  cars, people on foot and bikes detected with every setting at its default (close-in
  mask, MTI, gains). Video recordings only, no IQ data. Isolation experiments skipped.
- **I - HDL offload** (2026-07-27 to 2026-09-26): register bank (I1), chirp NCO (I2),
  I3 skipped (the capture jitter was in the RX/DMA path), fabric dechirp with
  loopback delay 34 (I4), `fabric_ctl.py` + worker wiring (I4b), fake targets on the
  IF stream (I4c), /8 halfband decimation (I5), no frame sync in the fabric path (I6).
  Conclusions in `.claude/rules/hdl.md`, guides in `docs/archive/`.
- **Host DSP pass**: CFAR `box_sum`, circular Doppler axis,
  log-power sub-bin, velocity axis on `T_EFF`, ~20x faster fake-target synthesis, range
  FFT padding skipped. Rules in `.claude/rules/dsp.md`. Reference sessions
  `data/sessions/20261102-19*` (real RF, fake targets: TDM sawtooth, triangle + TDM,
  MTI toggle, software mode), all `replay --check` OK.

## Open small items

- [ ] MTI beyond mean subtraction: 2-pulse canceller (`x[k] - x[k-1]`, wider notch,
      3 dB SNR cost), then an exponential-average clutter map. The Part G field test
      needed no change, so only if clutter becomes a problem.
- [ ] Frame sync (software mode): earliest-peak-above-threshold lock instead of argmax.
- [ ] 1/R^2 amplitude realism via `soft_model.add_amplitude`.
- [ ] Offline soft model runs with MTI_EN both ways (detector self-test regression).
- [ ] I5b - ADI FIR reclaim, deferred until a fabric FFT runs short of DSPs (99/220
      used; a range + Doppler FFT is ~10-35, and a 2D FFT is bound by corner-turn
      BRAM, not DSPs). Delete the four unused ADI FIRs (44 DSPs; recipe in the archived
      Phase 5 guide, Section 4.2 - easy to miss: `logic_or/Op1 <- dac_valid_i0`, and
      `rm` the e200 `.dtb` after the dtsi edit). The halfband fold is done (folded FIR,
      48 -> 24). Done when software mode and `dechirp_verify.py --ab` still pass.
- [ ] Spur source (`docs/rf_and_regulatory.md` "Spurs riding the chirp"): halve
      `CHIRP_BW_HZ` (spur ranges double, targets stay), then power the board from
      another supply (which family is external).

---

## Part K - Phase 6: tracking, RF bench, patch antennas, 2T1R angle (active)

Spec: `docs/AntSDR_Phase6_Tracking_Antennas_Angle_Guide.md` ("G" = its sections).
Rewritten 2026-09-28: angle from two switched TX antennas (2T1R TDM), not two
receivers - 2R2T caps the E200 at 30.72 MSPS = ~6 m bins (G1.2). The phase is for
learning RF: VNA, soldering, an RF component, antennas. Software lane first; order
the RF kit (K4, K5) while it runs. Each lane starts with its reading in G13.
Fabric FFT/CFAR is not planned - decision gate in G11.

- [x] K0 - 2R2T boot check (2026-09-28): boots, fabric transparent, FS capped at
      30.72 MSPS, which moved the angle plan to 2T1R. Fallback in G App. B,
      details in `.claude/rules/hdl.md` "2R2T mode".
- [X] K1 - profile the host (G2), `offline/profile_cpi.py` (2026-09-28, fabric /8,
      sawtooth): `process_cpi` ~25 ms whatever the fake-target count K (CFAR 19 ms of
      it); `apply_if` ~2.5 ms per fake target (7 ms at K 0, 131 at K 50), so ~30 CPI/s
      at K 0 and 6 at K 50. Triangle + MTI doubles both. The optionals moved to the
      host DSP pass (Open small items); the GUI slot was dropped (2026-10-30: ~5 ms
      at 20 detections, in the GUI thread).
- [X] K2 - recorder + replay (G3, 2026-09-29): `online/recorder.py`, `offline/replay.py`
      (`--check` exact on a 60 s loopback session), `offline/playback.py`.
- [ ] K3 - tracker in (r, v) (G4): CV Kalman, GNN, M-of-N; tests (a)-(e), live
      fake targets tracked. The reference sessions' four spurs (fixed r, v != 0, so
      `dr/dt != v`) must never confirm.
- [x] K4 - VNA basics (G5), LiteVNA-64: owned gear predicted, measured.
- [X] K5 - soldering + J30 header (G6): fitted, GPIO_00-07 = sysfs 995-1002 (2026-10-13).
- [ ] K6 - field day 1 (G7, needs K2, weather): COTS only; delay sweep (expect
      61 +-1), software vs fabric /8; record everything; flip the
      `FABRIC_DECHIRP_EN` default.
- [X] K7 - TX switch (G8, 2026-10-19): HMC849A, FMC5 (`tdm_en`, `tx_sel` on J30); A/B 30 dB
      both ways incl. triangle + TDM, floor = the E200's TX1 -> RX1 leak
      (`hardware/bench_measurements.md`).
- [X] K8 - single patch on FR4 (G9.3): sim, fab, SMAs, VNA, eps_r. Closed 2026-10-23
      (`scripts/antenna_design/results.md`, K8 "Measured"): patch eps_r **4.61** (launch
      removed), line pair 4.85 (eps_eff 3.83, lambda_g 26.4 mm, 0.0195 dB/mm, tan d
      0.018-0.019). The 0.24 gap misses the 0.1 target; the lines' value is taken as a
      setup artefact (unverified) and K9 is designed at **4.6**. The patch value is
      robust to launch differences (4.37-4.65). SMA launch S11 -6 to -8 dB (sim -33),
      cause open (not fixed for K9).
- [ ] K9 - columns + bench (G9.4-9.5): TX 2x(1x8) at lambda/2, RX 1x8; S11,
      coupling, isolation vs spacing, gain +-1 dB, pattern + A-B phase vs angle.
      Re-tuned at eps_r 4.6 2026-10-23 (`results.md`, K9; run files in
      `scripts/antenna_design/column/`): 10.8 dBi, S11 -18 dB, tilt -0.7 deg.
      Boards ordered 2026-10-26 (KiCad + Gerbers in `scripts/antenna_design/tx/`, `rx/`):
      JLCPCB FR4 TG135 as K8, EuroPacket, expected ~2026-11-06..12. Each board carries
      the K8 patch on a tab (witness for this batch's eps_r); if the tab is cut off, cut
      before the turntable calibration (9.5). K8's launch was left as is: trim fc to the
      column's launch-removed resonance.
      Turntable (9.5) on the LiteVNA, not the E200: recipe in
      `scripts/antenna_design/k9_turntable.md` (2026-11-02). In the radar the E200's
      TX1 -> RX1 leak lands ~1 range bin from the path, common to A and B and only ~5-10 dB
      under it (estimate from K7): psi off by tens of deg; under 1 deg at 30 deg needs it
      ~30 dB down. The VNA gives the curve's shape. The radar adds one offset, constant to
      ~1 deg of psi (the E200 TX1 port against the switch's state-dependent RFC match,
      -8 / -10 dB): psi0, set on field day 2 (K11) from a corner reflector carried
      radially along boresight at >= 1.5 m/s, >= 20 m out, MTI on (moving, so MTI removes
      the leak, TX-RX coupling and clutter from its cell). K10's cable test skipped step 0
      and the in-rig VNA, so that offset model is unconfirmed: psi0 comes from the field.

- [x] K10 - angle on the host (G10.1, 2026-10-29..11-02): `dsp.tdm_phasor` +
      `estimate_angle`, `FakeTarget.theta0`, Sector-PPI. Fake targets at +-30 deg within
      1 deg at 0 and +-20 m/s, sawtooth and triangle (`test_target_sim.py`); TDM adds ~3 ms
      to `process_cpi`, `apply_if` doubles. Cable test (sessions `20261102-201835` pad,
      `-211213` baseline, `-212202` saver): sign confirmed (`CLAUDE.md`), psi std ~0.03 deg,
      no A/B offset; the saver step read 19 deg over its bare VNA phase, in-rig VNA and
      step 0 skipped. Rules in `.claude/rules/dsp.md`.

- [ ] K11 - tracker in (x, y) + field day 2 (G10.2): EKF on (r, theta, v_r);
      patches on air, one change at a time. psi0 first, from the walked corner
      reflector (K9).

---

## Part H - PA + LNA + BPF (deferred purchases)

Part G worked without any of it, so buy only if a real range limit shows up, and
only into a dummy load, a cage, or with an amateur licence. The LNA likely pays off before the PA (NF buys SNR
linearly, TX power buys range^(1/4)).

- [ ] LNA at the RX antenna (sets system NF, ~+20 dB / NF ~1.3 dB class) + 3-6 dB
      pad between LNA and RX port. Re-run MGC window sweep.
- [ ] BPF 5725-5875 MHz on RX (blocks WiFi/LTE blockers once the LNA raises gain).
- [ ] PA last: FPV 2W class, 100% duty (FMCW!) -> heatsink + fan; bias sequencing
      via Zynq GPIO (Vg before Vd, never enable without load) per the block diagram.
- [ ] TX harmonic LPF (11.6 GHz) before the antenna once the PA is in.

---

## TODO - markdown cleanup

Too many markdown files. Fold or delete; git history keeps everything. Update the
`CLAUDE.md` project map in the same change.

- [ ] `docs/tracking_algorithms.md`: fold the verdict and reading into Sections 4 / 13.3
      when K3 starts, then delete.
- [ ] `scripts/antenna_design/eps_r_sweep.md`: generated; let `sweep_eps_r.py` print the
      table and keep only the PNG.