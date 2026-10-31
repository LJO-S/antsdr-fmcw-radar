
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
- [ ] Host DSP pass (2026-10-30), in order. Timings on a loaded machine, compare
      ratios. Steps 2-6 change detections, so old recordings stop replaying exact:
      each step replays the reference session and explains every changed detection.
      
      - [x] 0. Reference sessions (2026-10-31, `data/sessions/20261031-0923*`, fabric
            TDM sawtooth, 3 fake targets): `092300` (+-17.7 m/s) and `092343`
            (+-65 m/s), real RF (TX -> cable -> 30 dB pad -> RX); `092417`
            (+-65 m/s) digital loopback, noise off. All
            `replay --check` OK.
      
      - [x] 1. CFAR (2026-10-30): the 19x19 `convolve` -> `dsp.box_sum` (outer -
            guard, direct `correlate1d` sums in float64, cast back). ~22 -> ~3 ms per
            map, triangle + TDM `process_cpi` 70 -> 34 ms. Identical detections in
            650 noise maps, tones 20-160 dB, 300 `process_cpi` blocks (all modes);
            replay-safe. Why not `uniform_filter`: see `CLAUDE.md` "Detection pipeline".
      
      - [x] 2. Doppler wrap (2026-10-31): `nms`, `box_sum` (axis 0), `subbin_refine`
            rows and the triangle pairing (`dsp.dilate_doppler`) wrap the Doppler
            axis; `dsp.wrap_velocity` keeps refined v in [-vmax, vmax). New suite
            `common/test_dsp.py`. Replay: `092300` unchanged. `092343` / `092417`
            (targets at +-vmax): the edge duplicates (rows 127 + 0) are merged into one
            refined detection every CPI. Every other changed detection is in row 0:
            8 real targets no longer masked when two edge targets cross in range
            (`092343`), and 415 ridge cells in noise-off loopback (`092417`, the
            known CFAR-without-noise effect, row 64 already showed it). Outside
            the edge rows the detections are bitwise identical (also in 200
            synthetic blocks across all modes). Noise-only false alarms do not rise.
            Timing unchanged.
      
      - [x] 3. Sub-bin fit on log power (2026-10-31): `dsp._log_parabola_offset`,
            clipped to +-1. Blackman max error 0.09 -> 0.007 bin. Synthetic (all
            modes) high-SNR RMS ~0.065 -> ~0.005 bin; low SNR never worse (triangle
            range 0.19 -> 0.07). Replay: same detections and bitwise theta, only r/v
            move (< 0.33 bin). `092300` vs truth: range RMS 0.076 -> 0.015 bin
            (0.23 -> 0.05 m), velocity 0.010 -> 0.003 bin. Truth for sawtooth range
            must include the range-Doppler coupling `f_d * c / 2S` (0.4 bin at
            100 m/s).
      
      - [x] 4. Velocity axis on `T_EFF` (2026-10-31): `build_cpi_context` `T_rep`,
            `MAX_VELOCITY`, "Velocity Resolution", both test tolerances. `092300` vs
            truth: v bias -0.073 % -> -0.003 %; every v scales by exactly 5660/5656,
            r and theta bitwise. In `092343` / `092417` the +-65 m/s fake targets
            clip to the new, 0.07 % higher `MAX_VELOCITY`, now exactly the Nyquist
            edge, so which side v (and with it the sign of theta) lands on is a coin
            toss (edge ambiguity, not a bug). Use targets inside the span for step 7.
      
      - [x] 5. Fake-target synthesis speedup (2026-10-31). `apply_if`: one per-row x
            per-sample outer product per target, 5.6 -> 0.3 ms per target sawtooth,
            21 -> 1.1 ms triangle+TDM. `apply_doppler_shift` (software `apply()`,
            soft model): blocked phasor, 29 -> 4.7 ms per target. `apply_noise`:
            float32 draws from `TargetSim`'s own Generator (`a_seed`), 38 -> 14 ms
            software block. Output equals a float64 reference of the old formula to
            ~1e-6 x RMS in all modes; replay `092300` / `092343` same detections
            (dr, dv < 1e-6 bin, dtheta < 1e-4 deg). `092417` (noise-off loopback)
            swaps ~3 rounding-floor spurs per CPI below -127 dB; every real
            target matches.
      
      - [x] 6. Range FFT padding: skipped (2026-10-31), not worth it now. Idle
            machine: 707 -> 720 saves 0.9 ms per map (13-15 % of `process_cpi` at
            /8), but moves the range bins to 2.95 m. Worth it only in software or
            fabric /1 (5660 -> 5670: 33 -> 18 ms per map). If ever: pad the range FFT
            to `scipy.fft.next_fast_len(N_IF)`, axis from the padded length.
      
      - [ ] 7. Record a new reference session; `replay --check` passes.

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
      at K 0 and 6 at K 50. Triangle + MTI doubles both. The optionals moved to the
      host DSP pass (Open small items); the GUI slot was dropped (2026-10-30: ~5 ms
      at 20 detections, in the GUI thread).
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
      
      - [x] 3. Done 2026-10-30: `dsp.tdm_phasor` + `dsp.estimate_angle`, fields
            `ANGLE_PSI0_DEG` / `TDM_TX_ANT_SEP_M`, tested in step 4. Spec as built:
            `process_cpi` with `TDM_EN`: B rows through the same steps as A (fast-
            time mean, MTI, window, FFT, close-in mask). CFAR and the displayed map
            on `|X_A|^2 + |X_B|^2` (`cfar_ca_2d` squares its input: pass the
            root-sum-square). Per detection, at its cell:
            `dphi = angle(X_B * conj(X_A))`
            `psi = wrap(dphi - pi * k_d / CHIRP_REPS - psi0)` to (-pi, pi]
            `theta = asin(clip(-psi * lambda / (2*pi*d), -1, 1))`
            `k_d` = signed Doppler bin + sub-bin offset; `pi * k_d / CHIRP_REPS` is
            `2*pi*f_d*T_rep/2`, one A-to-B gap, so it holds with triangle too.
            Sub-bin offset from a parabola on **log** power with circular neighbours
            (`subbin_refine` was linear then; since Host DSP pass steps 2-3 it is
            log and circular too). Then wrap `k_d` into
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
      
      - [x] 6. GUI: x-y scatter next to the RD maps, theta in the detections table.
            Loopback fake targets at +-30 deg in the live app.
            Done 2026-10-30: Sector-PPI (rings, spoke angles, range ticks; follows
            `MAX_RANGE`) + "Azimuth [deg]" column, TDM only. Live loopback passed:
            sign, Doppler term at +-20 m/s, triangle, `ANGLE_PSI0_DEG` shift,
            record + `replay --check`.
      
      - [ ] 7. Cable test (no Doppler, no antennas). TX1 -> switch 1 RFC; RF1, RF2 ->
            two cables -> switch 2 RF1, RF2; switch 2 RFC -> RX1, no pad. Switch 2
            runs in reverse (passive FETs, reciprocal) as the combiner. Both V_CTL on
            J30 pin 3 (`tx_sel`), RF1 to RF1 (both units same polarity, K7). VDD: 2 x
            ~60 mA from J30 pin 1, check the rail first; a separate supply must be up
            before `tx_sel` drives (V_CTL <= VDD + 1 V). TX -30 dB
            (`SDR_TX_GAIN_MAX_DB` -20), fabric /8, `TDM_EN`, MTI off (the path is
            static). `FABRIC_DECHIRP_DELAY` ~41 moves the path out of the close-in
            mask to ~53 m. The board leak moves with it (same bin), ~30 dB under and
            common to A and B: a static offset inside psi0. The off arm passes two
            isolations (~-80 dB): no switch-leak bias. Record every run (K2). This
            psi0 is the cable's, not the antennas' (K9 turntable).
            Why not a tee or a Wilkinson (2026-10-31): the radar-vs-VNA psi gap comes
            from the E200 ports bouncing against the switch modules' poor match (-7
            to -10 dB), not from the combiner. Monte Carlo with the K7 module numbers
            and E200 ports at |G| 0.3 (a guess), rms / 95 %: tee 4.2 / 8.0 deg,
            Wilkinson 3.2 / 6.2, second switch 4.4 / 8.4.
            0. VNA S11 of the E200 RX1 and TX1 ports, board powered (RX gain as in
               the test, TX at -90 dB): replaces the |G| 0.3 guess; the gap scales
               with it, each port about half.
            a. VNA prediction: S21 switch 1 RFC -> switch 2 RFC, both jumpers on A,
               then both on B: `psi_pred = -(angle(S21_B) - angle(S21_A))` (minus:
               conj(RX)).
            b. Radar, `ANGLE_PSI0_DEG` 0: psi of the path over >= 100 CPIs. Mean vs
               psi_pred within ~+-8 deg (the E200 ports are not 50 ohm), std =
               stability. Then set `ANGLE_PSI0_DEG` to the mean psi, so theta reads 0.
            c. An SMA adapter into arm B, repeat a and b: both move by the same step
               with the same sign. VNA the adapter first: its step must sit well away
               from 0 and 180 deg (1 cm of PTFE ~ 100 deg at 5.8 GHz), else the sign
               test means nothing. Expected: a longer B path raises psi and lowers
               theta (target on A's side). Write the sign into `CLAUDE.md` next to
               the Doppler sign.
            Before 7: switch-only S21 (RFC -> RF1, RF2) on the VNA; TX1 -> >= 30 dB
            pad -> RX1 with `TDM_EN` (same delay trick): A and B share the path, so
            psi ~0 and its std is the noise floor.
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