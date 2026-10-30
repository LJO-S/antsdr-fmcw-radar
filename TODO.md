
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

Current state (2026-10-26): Parts A-G and I are done. **Part K (Phase 6) is active**:
K0-K2, K4, K5, K7 and K8 done. FMC5 (TDM + `tx_sel` on J30) is on hardware, and fabric /8
has run on cable RF (K7 bench) but not yet on air (K6). K9 boards ordered (due
~2026-11-06..12). Next: K10 software (angle) while they ship, K3 (tracker); K6 waits
on weather. Part H stays deferred. Part J blocks nothing and can run in parallel.

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
- [ ] Velocity axis on `T_EFF`, not `CHIRP_DUR_S`: the chirp period is `SWEEP_LEN / FS`,
      but `dsp.build_cpi_context` (`T_rep`), `config.MAX_VELOCITY`, the "Velocity
      Resolution" row and `test_target_sim.py` use `CHIRP_DUR_S`. Fabric /8 reads v
      0.07 % low (0.12 % worst case); software mode exact at the defaults. Angle is
      unaffected (Doppler term in bins). Found in the K10 exercise, 2026-10-28.
- [ ] Sub-bin fit on log power: `subbin_refine` fits its parabola on linear power (the
      `cfar_ca_2d` output), which reads up to ~0.085 bin low with Blackman (true 0.25
      -> 0.166): ~0.26 m in range, ~0.17 m/s in sawtooth velocity. On log power it is
      nearly exact (0.25 -> 0.256). Matters against the K3 tracker's `sr` 0.7 m /
      `sv` 0.5 m/s. Found in the K10 exercise, 2026-10-28.
- [ ] Doppler wrap in NMS: `nms` (`maximum_filter`, `mode="nearest"`) and `subbin_refine`
      do not wrap the Doppler axis, so a target near +-`MAX_VELOCITY` whose peak spans
      rows `REPS-1` and 0 is detected twice (+63.6 and -64.7 m/s at 0.98 `MAX_VELOCITY`,
      TDM). Fix: `mode=("wrap", "nearest")` in `nms`, circular rows in `subbin_refine`.
      Changes detections, so old recordings no longer replay exact. Found in K10 step 3,
      2026-10-30.

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
- [X] K1 - profile the host (G2), `offline/profile_cpi.py` (2026-09-28, fabric /8,
      sawtooth): `process_cpi` ~25 ms whatever the fake-target count K (CFAR 19 ms of
      it); `apply_if` ~2.5 ms per fake target (7 ms at K 0, 131 at K 50), so ~30 CPI/s
      at K 0 and 6 at K 50. Triangle + MTI doubles both. All optional:
      - [ ] `apply_if`: per-chirp slow phasor x fast phasor, or one matmul for all targets.
      - [ ] CFAR: 19x19 `convolve` -> two `uniform_filter` box sums (outer - guard).
      - [ ] Time the GUI slot (`det_table` builds 4 `QTableWidgetItem`s per detection).
- [X] K2 - recorder + replay (G3, 2026-09-29): `online/recorder.py`, `offline/replay.py`
      (`--check` exact on a 60 s loopback session), `offline/playback.py`.
- [ ] K3 - tracker in (r, v) (G4): CV Kalman, GNN, M-of-N; tests (a)-(e), live
      fake targets tracked.
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

- [ ] K10 - angle on the host (G10.1, read G13.4 first). Can start before K9. Done
      when fake targets at +-30 deg come back within 1 deg at 0 and +-20 m/s, and
      the cable test agrees with the VNA. Sawtooth + TDM first, triangle after.

      - [x] 1. Exercise (G13.4, 2026-10-29), `scripts/angle/tdm_exercise.py`: 8.9 deg at
            20 m/s without the Doppler term, < 0.003 deg with it up to +-MAX_VELOCITY;
            sub-bin 0.22 deg (integer) -> 0.003 (log); sigma matches G10.1 at >= 20 dB,
            wraps below ~5 / 10 / 20 dB at 0 / 30 / 60 deg; switch leak <= 1.4 deg at
            30 dB (calibrates out).
      
      - [X] 2. Fake targets: `FakeTarget.theta0` (deg, > 0 = towards antenna B).
            `apply_if` with `TDM_EN`: rows with `row % LEGS_PER_PERIOD >=
            LEGS_PER_PERIOD // 2` are B, times
            `exp(-1j*2*pi*TDM_TX_ANT_SEP_M*sin(theta)/lambda)`, the same d and lambda as
            step 3 (d is not exactly lambda/2: `pi*sin(theta)` leaves a 0.07 deg bias
            at 30 deg). Minus: the IF is conj(RX), the same flip as the Doppler sign.
            The continuous Doppler already gives B its motion phase; add nothing for it.
      
      - [ ] 3. `process_cpi` with `TDM_EN`: B rows through the same steps as A (fast-
            time mean, MTI, window, FFT, close-in mask). CFAR and the displayed map
            on `|X_A|^2 + |X_B|^2` (`cfar_ca_2d` squares its input: pass the
            root-sum-square). Per detection, at its cell:
            `dphi = angle(X_B * conj(X_A))`
            `psi = wrap(dphi - pi * k_d / CHIRP_REPS - psi0)` to (-pi, pi]
            `theta = asin(clip(-psi * lambda / (2*pi*d), -1, 1))`
            `k_d` = signed Doppler bin + sub-bin offset; `pi * k_d / CHIRP_REPS` is
            `2*pi*f_d*T_rep/2`, one A-to-B gap, so it holds with triangle too.
            Sub-bin offset from a parabola on **log** power with circular neighbours
            (`subbin_refine` is linear and skips edge rows: up to 0.08 bin, ~0.04
            deg; r and v keep it, so replays stay exact). Then wrap `k_d` into
            [-REPS/2, REPS/2), else a target at +63.8 bins reads -64.2 and psi is
            off by pi. Reference: `scripts/angle/tdm_exercise.py` `estimate_theta`.
            `lambda` from `CHIRP_FC_HZ`. New config fields `ANGLE_PSI0_DEG` (psi0, a
            phase, 0) and `TDM_TX_ANT_SEP_M` (0.0259), group `angle`. Targets gain `theta`
            (deg) only with `TDM_EN`, so non-TDM replays are unchanged; x and y are
            the GUI's (step 6) and K11's EKF works on (r, theta, v_r). Past `MAX_VELOCITY` the Doppler term takes the
            wrong alias: psi off by pi, angle mirrored.
      
      - [x] 4. Test next to the v-sign check in `online/test_target_sim.py`: theta0 in
            {-30, 0, +30} x v0 in {-20, 0, +20}, |error| < 1 deg; with the Doppler
            term disabled, ~9 deg at 20 m/s (the term is live). `run_tests.py` green;
            `profile_cpi.py` with TDM: extra ms vs the K1 table.
            Done 2026-10-30 (`test_tdm_angle`, `test_tdm_doppler_term_is_live`;
            `profile_cpi.py --tdm`). Sawtooth /8, median ms, K1 -> TDM:

            | | K 0 | K 50 |
            |---|---|---|
            | `process_cpi` | 28.6 -> 28.1 | 25.2 -> 28.2 |
            | of which RSS + angle | 0.4 | 0.4 |
            | `apply_if` | 7.0 -> 14.1 | 136 -> 279 |
            | CPI/s bound | 28 -> 24 | 6.2 -> 3.3 |

            TDM costs ~3 ms in `process_cpi` (B's FFTs and prep); the angle is 0.2 ms,
            flat in detections. `apply_if` doubles (2x rows, ~5.3 ms per fake target).
      
      - [x] 5. Triangle + TDM (A-up to B-up and A-down to B-down are both 2T, same
            formula). `MAX_VELOCITY` 32 m/s. Done 2026-10-30: `dsp.tdm_phasor` per
            map, `dsp.estimate_angle` on the phasor (or a sum), `dsp.target_dicts`.
            "up" from the up maps, "down" from the down maps, "both" sums the two
            phasors (independent looks: sigma / sqrt(2), 1.3-1.6x measured). Down
            maps are `fft2(conj(...))`: the phasor is conjugated, and they are read
            on their own rows `(REPS - row) % REPS` (the inverse of
            `align_down_doppler`), where the gap term holds unchanged. psi0 is
            removed after the sum and calibrates both ramps. Tests
            `test_tdm_triangle_angle`, `test_tdm_triangle_down_only_angle`;
            `profile_cpi.py --triangle --tdm` mirrors it.
      
      - [ ] 6. GUI: x-y scatter next to the RD maps, theta in the detections table.
            Loopback fake targets at +-30 deg in the live app.
            GUI done 2026-10-30 (bird's-eye plot + "Azimuth [deg]" column, TDM only,
            checked offscreen on `process_cpi` output); the live-app check is open.
      
      - [ ] 7. Cable test (no Doppler, no antennas). TX1 -> switch RFC; RF1, RF2 ->
            two cables -> tee -> RX1, no pad. TX -30 dB (`SDR_TX_GAIN_MAX_DB` -20),
            fabric /8, `TDM_EN`, MTI off (the path is static). `FABRIC_DECHIRP_DELAY`
            ~41 moves the path out of the close-in mask to ~53 m. The board leak
            moves with it (same bin), ~30 dB under and common to A and B: a static
            offset inside psi0. Record every run (K2). This psi0 is the cable's, not
            the antennas' (K9 turntable).
            a. VNA prediction: S21 RFC -> tee output, switch jumper on A, then B:
               `psi_pred = -(angle(S21_B) - angle(S21_A))` (minus: conj(RX)).
            b. Radar, `ANGLE_PSI0_DEG` 0: psi of the path over >= 100 CPIs. Mean vs
               psi_pred (within a few deg: the E200 ports are not 50 ohm), std =
               stability. Then set `ANGLE_PSI0_DEG` to the mean psi, so theta reads 0.
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

- [ ] `docs/tracking_algorithms.md`: fold the verdict and reading into Sections 4 / 13.3
      when K3 starts, then delete.
- [ ] `scripts/antenna_design/eps_r_sweep.md`: generated; let `sweep_eps_r.py` print the
      table and keep only the PNG.
- [ ] `src/python/README.md`: duplicates the `CLAUDE.md` project map; delete.
- [ ] `docs/archive/` Phases 1-3: conclusions are in `CLAUDE.md`; delete. Phases 4-5 stay
      until I5b (the FIR reclaim recipe is Phase 5 Section 4.2).