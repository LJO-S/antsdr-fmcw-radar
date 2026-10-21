
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
their design conclusions move to `CLAUDE.md`, which is the maintained map - don't
grow narrative back in here as parts close.

Current state (2026-10-20): Parts A-G and I are done. **Part K (Phase 6) is active**:
K0-K2, K4, K5 and K7 done. FMC5 (TDM + `tx_sel` on J30) is on hardware, and fabric /8
has run on cable RF (K7 bench) but not yet on air (K6). Next: K10 software (angle),
K3 (tracker); K8 boards ordered; K6 waits on weather. Part H stays deferred. Part J
blocks nothing and can run in parallel.

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
  Conclusions in `CLAUDE.md` ("Firmware / HDL track"), guides in `docs/archive/`.

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
      details in `CLAUDE.md` "2R2T mode".
- [X] K1 - profile the host (G2): ms per `process_cpi` stage vs detection count,
      plus the GUI slot; fix what grows with detections.
      `offline/profile_cpi.py` (2026-09-28), fabric /8, sawtooth, median ms:

      | K fake targets     |    0 |    5 |   20 |    50 |
      |--------------------|------|------|------|-------|
      | prep + FFTs        |  5.3 |  2.5 |  2.5 |   2.5 |
      | close-in mask + dB |  1.5 |  1.1 |  1.4 |   1.4 |
      | CFAR               | 19.0 | 18.8 | 18.8 |  18.8 |
      | NMS                |  1.6 |  1.7 |  1.7 |   1.7 |
      | sub-bin + dicts    |  0.2 |  0.2 |  0.2 |   0.2 |
      | `process_cpi`      | 26.6 | 24.0 | 24.4 |  24.4 |
      | `apply_if`         |  7.0 | 18.5 | 56.1 | 131.1 |
      | CPI/s bound        | 29.8 | 23.5 | 12.4 |   6.4 |

      Triangle + MTI doubles it: `process_cpi` ~50 ms, `apply_if` 269 ms at K=50.
      Nothing in `process_cpi` grows with K; `apply_if` (fake targets, ~2.5 ms each,
      one full-block `np.exp` per target) does. Since fake targets are optional, these are all optional:
      - [ ] `apply_if`: per-chirp slow phasor x fast phasor, or one matmul for all targets.
      - [ ] CFAR: 19x19 `convolve` -> two `uniform_filter` box sums (outer - guard).
      - [ ] Time the GUI slot (`det_table` builds 4 `QTableWidgetItem`s per detection).
- [X] K2 - recorder + replay (G3): raw blocks + index + config + live detections
      per session; replay reproduces the detections exactly. Code done 2026-09-29
      (`online/recorder.py`, `offline/replay.py`, Record checkbox; 12/12
      `test_recorder.py`, and the worker loop headless against a fake radio in both
      modes). Visual playback: `python -m offline.playback [dir]`. On the board, a
      60 s loopback session with `SDR_LOOPBACK_NOISE_SNR_DB = 0` passes
      `python -m offline.replay <dir> --check`.
- [ ] K3 - tracker in (r, v) (G4): CV Kalman, GNN, M-of-N; tests (a)-(e), live
      fake targets tracked.
- [x] K4 - VNA basics (G5), LiteVNA-64: owned gear predicted, measured.
- [X] K5 - soldering + J30 header (G6): practice joints, fit the 10-pin header,
      map GPIO_00-07 from Linux (sysfs gpio 995-1002).
      Map verified 2026-10-13: all 8 pins toggle from sysfs, match the schematic.
- [ ] K6 - field day 1 (G7, needs K2, weather): COTS only; delay sweep (expect
      61 +-1), software vs fabric /8; record everything; flip the
      `FABRIC_DECHIRP_EN` default.
- [X] K7 - TX switch (G8): HMC849A module on the VNA (IL, isolation, S11);
      `tdm_en` + chirp-pair period + `tx_sel` on J30, MAGIC FMC5, VUnit; bench
      test with port A -> pad -> RX, port B terminated.
      8.4 passed 2026-10-19 (no pad): A/B 30 dB both ways, also triangle + TDM;
      floor = the E200's TX1 -> RX1 leak (`hardware/bench_measurements.md`).
- [ ] K8 - single patch on FR4 (G9.3): openEMS, fab, solder SMAs, S11, back out
      eps_r. Order the PCB early; do K7 while it ships.
      Simulation done 2026-10-05 (`scripts/antenna_design/`): eps_r 4.4 geometry at
      h 1.5 mm, mesh converged; at the board edge the S11 null is at 5.801 GHz, worst
      in band -16.3 dB; 59 % efficiency, 5.7 dBi. In band for eps_r ~4.3-4.5, -60 MHz
      per +0.1 (`eps_r_sweep.md`; `--measured` turns a VNA reading into eps_r).
      KiCad done 2026-10-05: patch board + line-pair board, Gerbers checked
      (`scripts/antenna_design/README.md`, "Boards"). Ordered (by 2026-10-13). Left: solder, measure.
- [ ] K9 - columns + bench (G9.4-9.5): TX 2x(1x8) at lambda/2, RX 1x8; S11,
      coupling, isolation vs spacing, gain +-1 dB, pattern + A-B phase vs angle.
- [ ] K10 - angle on the host (G10.1, read G13.4 first). Can start before K9. Done
      when fake targets at +-30 deg come back within 1 deg at 0 and +-20 m/s, and
      the cable test agrees with the VNA. Sawtooth + TDM first, triangle after.

      - [ ] 1. Exercise (G13.4): numpy 2T1R TDM, one target at (r, v, theta). Angle
            error vs v with and without the Doppler term; sigma_theta vs SNR vs G10.1.
            Expect ~9 deg at 20 m/s without the term (773 Hz x 100 us = 28 deg of psi).
      
      - [ ] 2. Fake targets: `FakeTarget.theta0` (deg, > 0 = towards antenna B).
            `apply_if` with `TDM_EN`: rows with `row % LEGS_PER_PERIOD >=
            LEGS_PER_PERIOD // 2` are B, times `exp(-1j*pi*sin(theta))`. Minus: the IF
            is conj(RX), the same flip as the Doppler sign. The continuous Doppler
            already gives B its motion phase; add nothing for it.
      
      - [ ] 3. `process_cpi` with `TDM_EN`: B rows through the same steps as A (fast-
            time mean, MTI, window, FFT, close-in mask). CFAR and the displayed map
            on `|X_A|^2 + |X_B|^2` (`cfar_ca_2d` squares its input: pass the
            root-sum-square). Per detection, at its cell:
            `dphi = angle(X_B * conj(X_A))`
            `psi = wrap(dphi - pi * k_d / CHIRP_REPS - psi0)` to (-pi, pi]
            `theta = asin(clip(-psi * lambda / (2*pi*d), -1, 1))`
            `k_d` = signed Doppler bin + sub-bin offset; `pi * k_d / CHIRP_REPS` is
            `2*pi*f_d*T_rep/2`, one A-to-B gap, so it holds with triangle too.
            `lambda` from `CHIRP_FC_HZ`. New config fields `ANGLE_CAL_DEG` (psi0, 0)
            and `ANGLE_D_M` (0.0259), group `angle`. Targets gain `theta`,
            `x = r sin(theta)`, `y = r cos(theta)` only with `TDM_EN`, so non-TDM
            replays are unchanged. Past `MAX_VELOCITY` the Doppler term takes the
            wrong alias: psi off by pi, angle mirrored.
      
      - [ ] 4. Test next to the v-sign check in `online/test_target_sim.py`: theta0 in
            {-30, 0, +30} x v0 in {-20, 0, +20}, |error| < 1 deg; with the Doppler
            term disabled, ~9 deg at 20 m/s (the term is live). `run_tests.py` green;
            `profile_cpi.py` with TDM: extra ms vs the K1 table.
      
      - [ ] 5. Triangle + TDM: angle from the up map (A-up to B-up is 2T, same
            formula). Down-only detections get none for now. `MAX_VELOCITY` 32 m/s.
      
      - [ ] 6. GUI: x-y scatter next to the RD maps, theta in the detections table.
            Loopback fake targets at +-30 deg in the live app.
      
      - [ ] 7. Cable test (no Doppler, no antennas). TX1 -> switch RFC; RF1, RF2 ->
            two cables -> tee -> RX1, no pad. TX -30 dB (`SDR_TX_GAIN_MAX_DB` -20),
            fabric /8, `TDM_EN`, MTI off (the path is static). `FABRIC_DECHIRP_DELAY`
            ~41 moves the path out of the close-in mask to ~53 m. The board leak
            moves with it (same bin), ~30 dB under and common to A and B: a static
            offset inside psi0. Record every run (K2). This psi0 is the cable's, not
            the antennas' (K9 turntable).
            a. VNA prediction: S21 RFC -> tee output, switch jumper on A, then B:
               `psi_pred = -(angle(S21_B) - angle(S21_A))` (minus: conj(RX)).
            b. Radar, `ANGLE_CAL_DEG` 0: psi of the path over >= 100 CPIs. Mean vs
               psi_pred (within a few deg: the E200 ports are not 50 ohm), std =
               stability. Then set `ANGLE_CAL_DEG` so theta reads 0.
            c. An SMA adapter into arm B, repeat a and b: both move by the same step
               with the same sign. Expected: a longer B path raises psi and lowers
               theta (target on A's side). Write the sign into `CLAUDE.md` next to
               the Doppler sign.
- [ ] K11 - tracker in (x, y) + field day 2 (G10.2): EKF on (r, theta, v_r);
      patches on air, one change at a time.

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

- [ ] `docs/vna_soldering_buy_list.md`: delete once the K4/K5 kit is bought; anything
      worth keeping goes to `hardware/materials.md`.
- [ ] `docs/tracking_algorithms.md`: fold the verdict and reading into Sections 4 / 13.3
      when K3 starts, then delete.
- [ ] `scripts/antenna_design/eps_r_sweep.md`: generated; let `sweep_eps_r.py` print the
      table and keep only the PNG.
- [ ] `scripts/antenna_design/results.md`: cut to the fabricated design (the h 1.6 mm
      redesigns are stale).
- [ ] `src/python/README.md`: duplicates the `CLAUDE.md` project map; delete.
- [ ] `docs/archive/` Phases 1-3: conclusions are in `CLAUDE.md`; delete. Phases 4-5 stay
      until I5b (the FIR reclaim recipe is Phase 5 Section 4.2).