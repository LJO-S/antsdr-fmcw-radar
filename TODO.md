
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

Current state (2026-10-23): Parts A-G and I are done. **Part K (Phase 6) is active**:
K0-K2, K4, K5, K7 and K8 done. FMC5 (TDM + `tx_sel` on J30) is on hardware, and fabric /8
has run on cable RF (K7 bench) but not yet on air (K6). Next: K9 boards (KiCad, order),
K10 software (angle) while they ship, K3 (tracker); K6 waits on weather. Part H stays
deferred. Part J blocks nothing and can run in parallel.

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
- [X] K8 - single patch on FR4 (G9.3): sim, fab, SMAs, VNA, eps_r. Closed 2026-10-23
      (`scripts/antenna_design/results.md`, K8 "Measured"): patch eps_r **4.61** (launch
      removed), line pair 4.85 (eps_eff 3.83, lambda_g 26.4 mm, 0.0195 dB/mm, tan d
      0.018-0.019). The 0.24 gap misses the 0.1 target; the lines' value is taken as a
      setup artefact (unverified) and K9 is designed at **4.6**. The patch value is
      robust to launch differences (4.37-4.65). SMA launch S11 -6 to -8 dB (sim -33),
      cause open (K9 step 0).
- [ ] K9 - columns + bench (G9.4-9.5): TX 2x(1x8) at lambda/2, RX 1x8; S11,
      coupling, isolation vs spacing, gain +-1 dB, pattern + A-B phase vs angle.
      Re-tuned at eps_r 4.6 2026-10-23 (`results.md`, K9; run files in
      `scripts/antenna_design/column/`): 10.8 dBi, S11 -18 dB, tilt -0.7 deg.

      Order recipe, KiCad 10 -> Gerbers. One project per board (`tx/`, `rx/` next
      to `line-pair/`); coordinates in mm from the feed edge on the board centre line.

      - [x] 0. The K8 launch (-6 to -8 dB, unexplained): skipped 2026-10-24, ordered as
            is; fc is trimmed to the column's resonance instead.
      
      - [X] 1. Footprints (openEMS venv, `scripts/antenna_design/`; done 2026-10-23):
            `python make_footprints.py --eps-r 4.6 --L 11.531 --lc 12.45 --tw 4.481 --tl 6.587`
            -> `antenna.pretty/` column + witness patch. Check the `.png` previews.
      
      - [X] 2. New project. Copy `line-pair/line-pair.kicad_dru` to `tx/tx.kicad_dru`
            (SMA pads may touch the edge). Preferences -> Manage Footprint Libraries ->
            Project Specific: nickname `antenna`, path `${KIPRJMOD}/../antenna.pretty`.
      
      - [X] 3. Schematic, per antenna: `Connector:Conn_Coaxial` (footprint
            `Connector_Coaxial:SMA_Molex_73251-1153_EdgeMount_Horizontal`), centre pin
            wired to a `Device:Antenna` (footprint `antenna:K9_column_N8_uniform` or
            `antenna:K8_patch_witness`), shield to GND (+ PWR_FLAG). Nets TX: COL_A,
            COL_B, WITNESS; RX: COL, WITNESS. Annotate, ERC.
      
      - [X] 4. Board Setup: stackup 2 layers, 1.6 mm, core 1.51 FR4, eps_r 4.6, tan d
            0.02 (documentation only). DRC severity "solder mask aperture bridges items
            with different nets" -> ignore (step 8 opens the whole top mask).
      
      - [X] 5. Update PCB from schematic (F8). Place -> Grid Origin at the feed edge on
            the centre line; Preferences -> Origins & Axes: display origin = grid origin.
            Place at rotation 0, antennas at x 0, each SMA at x 4.26 on its antenna's y.
            TX: col A y -12.95, col B y +12.95, witness y 63.678. RX: col y 0, witness
            y 50.728.
      
      - [X] 6. Edge.Cuts polygon: the column board plus the K8 patch board (51.842 x
            55.728, same ground margins) as a tab at the feed end. All SMAs on one edge;
            the tab's small asymmetry is calibrated out by the turntable (9.5).
            TX: (0,-35.814) (225.985,-35.814) (225.985,35.814) (51.842,35.814)
            (51.842,91.542) (0,91.542). RX: (0,-22.864) (225.985,-22.864)
            (225.985,22.864) (51.842,22.864) (51.842,78.592) (0,78.592).
      
      - [X] 7. B.Cu zone, net GND, over the whole board, pad connections solid. Fill (B),
            then view B.Cu alone: one plane, solid around every SMA pad and hole.
      
      - [X] 8. F.Mask: one filled rectangle over the whole board (top mask open, as
            simulated and as K8). Text on B.SilkS only: name, eps_r 4.6, date (no
            order-number text: JLCPCB left K8's unmarked). **M2 mounting holes (do not forget)**:
            `MountingHole:MountingHole_2.2mm_M2` near the corners, >= 10 mm from antenna
            copper; NPTH, so they land in the NPTH drill file (step 10).
            Optional art on B.SilkS (free at JLCPCB): lines >= 0.15 mm, off the SMA pads.
      
      - [X] 9. DRC: no errors, no unconnected items. Expected leftovers only: SMA
            courtyards over the edge, antenna footprints without courtyard. 3D view.
      
      - [ ] 10. File -> Fabrication Outputs -> Gerbers to `gerbers/`: F.Cu, B.Cu, F.Mask,
            B.Mask, B.SilkS, Edge.Cuts, Protel extensions on. Then **Generate Drill
            Files** in the same dialog (Excellon, mm, PTH and NPTH separate): without it
            the SMA ground holes are not made.
      
      - [ ] 11. Gerber Viewer: load the Gerbers and both drill files. Count 2 holes per
            SMA and the M2 holes; measure patch L 11.531, W 15.728, pitch 23.981, board 225.985 against
            `results.md`. Zip `gerbers/`.
      
      - [ ] 12. JLCPCB, TX and RX in one order (one batch, one fc): as K8 (FR-4
            TG135-140, 2 layers, 1.6 mm, 1 oz, HASL), marking option as for
            K8. Check the holes in their viewer before paying.




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