
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

Current state (2026-11-14): Parts A-G and I are done. **Parts K (Phase 6) and L
(Phase 7, tracking) are active**:
K0-K2, K4, K5, K7, K8, K10, L0 done; L1-L3 built, board checks and 2026-11-12/13 review
items open. FMC5 (TDM + `tx_sel` on J30) on hardware; fabric /8 has run on cable RF (K7
bench, K10) but not on air (K6). K9 boards arrived 2026-11-09, S-params measured,
turntable next.
**Next: K6 on first dry day** - only source of real data for L6a; tracker tuned on
synthetic noise until then. Meanwhile L2/L3 open items, K9 turntable, then L5. L4 full
layout can wait for L5.
Part H deferred. Part J (`docs/archive/optionals.md`) blocks nothing, can run in parallel.

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

## Part K - Phase 6: RF bench, patch antennas, 2T1R angle (active)

Spec: `docs/AntSDR_Phase6_Antennas_Angle_Guide.md` ("G" = its sections).
Rewritten 2026-09-28: angle from two switched TX antennas (2T1R TDM), not two
receivers - 2R2T caps the E200 at 30.72 MSPS = ~6 m bins (G1.2). The phase is for
learning RF: VNA, soldering, an RF component, antennas. Software lane first; order
the RF kit (K4, K5) while it runs. Each lane starts with its reading in G13.
Fabric FFT/CFAR is not planned - decision gate in G11. Tracking (K3, the K11 tracker)
moved to Part L on 2026-11-05.

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
- K3 - tracker in (r, v): moved to Part L (L1-L4), 2026-11-05.
- [x] K4 - VNA basics (G5), LiteVNA-64: owned gear predicted, measured.
- [X] K5 - soldering + J30 header (G6): fitted, GPIO_00-07 = sysfs 995-1002 (2026-10-13).
- [ ] K6 - field day 1 (G7, needs K2, weather): COTS only; delay sweep (expect
      61 +-1), software vs fabric /8; record everything; flip the
      `FABRIC_DECHIRP_EN` default. Gaps filled 2026-11-12:
  - [ ] MTI off for the static corner reflector (MTI removes it); find it by A/B (G7 step 1)
  - [ ] Range walk: a person out along boresight until lost, and back; `snr_db` vs range
        (G7 step 5)
  - [ ] The same plus a few cars behind a VAT-15A+ on RX: patch-class SNR, tests G1.3
        (G7 step 6)
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
      Boards (KiCad + Gerbers in `scripts/antenna_design/tx/`, `rx/`) measured on the VNA
      2026-11-09 (`results.md`, K9 "Measured"): batch eps_r **~4.8** not 4.6 (witness
      tab) -> expect 1.5-1.8 dB less gain, ~5 deg tilt at 5.8 GHz. Column S11 -6.5 to
      -8.4 dB worst in band (launch; S11 cannot set fc), coupling A-B -25.8 dB. Cut witness
      tab off only after turntable calibration (9.5).
      Turntable (9.5) on the LiteVNA, not the E200: recipe in
      `scripts/antenna_design/k9_turntable.md` (2026-11-02; **not in git and not on the
      desktop as of 2026-11-12**: commit it from the laptop). In the radar the E200's
      TX1 -> RX1 leak lands ~1 range bin from the path, common to A and B and only ~5-10 dB
      under it (estimate from K7): psi off by tens of deg; under 1 deg at 30 deg needs it
      ~30 dB down. The VNA gives the curve's shape. The radar adds one offset, constant to
      ~1 deg of psi (the E200 TX1 port against the switch's state-dependent RFC match,
      -8 / -10 dB): psi0, set on field day 2 (K11) from a corner reflector carried
      radially along boresight at >= 1.5 m/s, >= 20 m out, MTI on (moving, so MTI removes
      the leak, TX-RX coupling and clutter from its cell). K10's cable test skipped step 0
      and the in-rig VNA, so that offset model is unconfirmed: psi0 comes from the field.
  - [ ] Elevation cut on the turntable (`k9_turntable.md` 8): gives tilt -> fc (window in
        `CLAUDE.md`) and mount tilt
  - [ ] Commit `k9_turntable.md` from the laptop (the README and this file point to it)
  - [ ] Gain by the two-antenna method on the VNA too, not the E200: same leak (G9.5)
  - [ ] Redo the TX gain with the measured gain and the switch's 3.0 dB: sim says 0 dB,
        ~12 dBm EIRP, 2-3.5 dB under the cap (G1.3, G9.6)

- [x] K10 - angle on the host (G10.1, 2026-10-29..11-02): `dsp.tdm_phasor` +
      `estimate_angle`, `FakeTarget.theta0`, Sector-PPI. Fake targets at +-30 deg within
      1 deg at 0 and +-20 m/s, sawtooth and triangle (`test_target_sim.py`); TDM adds ~3 ms
      to `process_cpi`, `apply_if` doubles. Cable test (sessions `20261102-201835` pad,
      `-211213` baseline, `-212202` saver): sign confirmed (`CLAUDE.md`), psi std ~0.03 deg,
      no A/B offset; the saver step read 19 deg over its bare VNA phase, in-rig VNA and
      step 0 skipped. Rules in `.claude/rules/dsp.md`.

- [ ] K11 - field day 2 (G10.2): patches on air, one change at a time. psi0 first,
      from the walked corner reflector (K9). Records the sessions L6b validates on;
      the (x, y) tracker itself moved to Part L (L5).
  - [ ] **TX gain at every TX antenna swap**: ~-5 dB for the sector, 0 dB for the patches.
        0 dB into the sector is ~20 dBm EIRP, 6 dB over the cap (G9.6)
  - [ ] psi0 to +-3 deg of psi is enough: an error is mostly a rotation of the map (G10.2)
  - [ ] Short on SNR: the sector (V port) as RX, +5 dB over the column (G10.2)

---

## Part L - Phase 7: tracking (active, in parallel with Part K)

Spec: `docs/AntSDR_Phase7_Tracking_Guide.md` ("G7" = its sections). Split out of
Phase 6 on 2026-11-05. L0-L5 run now; L6 waits on K6 and K11. The `20261102-19*`
reference sessions (spur test, L1 check) are on the laptop, not the desktop.

- [x] L0 - reading + exercises (G7 4, 2026-11-09): `scripts/tracking/`. GNN is enough;
      EKF for L5 with `sa` 10. Results in G7 4.
- [x] L1 - `snr_db` per detection (G7 5, 0bc278d, rules in `dsp.md`): old sessions check
      exact (eight `20261102-*`, laptop 2026-11-13), +20 dB/decade of `amp`, new session
      `20261114-141729` (fabric, loopback, 762 CPIs) `replay --check` OK.
- [ ] L2 - tracker in (r, v), `common/tracking.py` (G7 6)
  - Built (3152b8f, fba89b4). `test_tracking` 17/17 on laptop, (i) on five reference
    sessions included (2026-11-13). Cost 0.3 ms/CPI at 4 tracks, 1.7 at 96.
  - Order matters ((g) first, or `sa` 10 turns suite red):
  - [x] Test (g) asserts a share (>= 70 % of 20 seeds); `TRACK_STD_A` now 10 (2026-11-14,
        G7 3.3), L6a tunes it by NIS
  - [ ] Tests (p) braking, (q) passing, (r) coloured range noise (also at walking speed,
        see floor item), (s) spur capture (G7 6.9)
  - 2026-11-13 review (synthetic, `sa` 10; recordings show none of it):
  - [ ] Spur leak: fixed-r stream with noise near `sr` / `sv` confirms briefly in 3-19 of
        20 runs of 60 s (G7 6.5 says 0.13 %); 2nd tentative track born on stream, they
        share its detections. Fixed 2026-11-14 in `Tracker.update`: no births inside the
        NMS window of a tentative track that has met M-of-N. Test (i) is now 60 s x 20
        seeds and fails 2 / 3 / 6 of 20 seeds at 2 / 10 / 40 m/s (old code 16 / 8 / 4):
        about Phi(-3) per born spur track (0.1-0.2 %, hundreds of births per run), so not
        the leak
  - [ ] Decide test (i): assert the confirmed share of born spur tracks (<= ~0.5 %), not
        "never"; check it still catches the old leak
  - [ ] A track can confirm on a CPI without a detection (age reaches W while coasting;
        `met` sticky). Require a hit and M-of-N on that CPI: session `211213` 67 confirmed
        IDs (22 under 1 s) -> 39 (5)
  - [ ] `abs(v_mean) / 2` fail floor gives slow targets no cover: walker at 1 m/s, 0.5 m
        rms / 1 s range wander keeps ID in 27 % of 60 s runs, 100 % with absolute
        1 m/s floor. Decide on L6a data
  - [ ] `TRACK_EN`: guide says True, `config.py` False. Flip with L4 (nothing draws tracks
        before; K6 tracks offline with `replay --track`) (G7 6.7)
- [ ] L3 - app, playback, replay (G7 7)
  - Built (fba89b4); rules in `app.md`. `replay --track` deterministic (2 runs identical,
    laptop 2026-11-13). Capture stamp fixed: 2 kernel buffers + `BlockClock` (`sdr.md`)
  - [ ] Live loopback: one stable track per fake target (board)
  - [ ] fps cost vs K1 (board)
- [ ] L4 - GUI layout A (G7 8)
  - Toolbar row; PPI left ~60 % (r-v plot there with TDM off); r-v plot + track list
    right
  - Tracks: ID + ~2 s tail, coasting dimmed, tentative toggle, click to select
  - RD maps to Signals; track lines in History
  - Done: live and playback in loopback; GUI slot re-measured
  - [ ] Number tracks at confirmation: IDs are spent at birth, 23-40/s
  - [ ] `_draw_history` (5 Hz) ran with the tab hidden, 15 ms at 5000 dots: likely the
        +40 ms stall every 0.2 s in recorded dt. Skipped when hidden (2026-11-14, draws on
        tab switch); still to do: cap dots, confirm on board
- [ ] L5 - tracker in (x, y) (G7 9)
  - EKF `[x, y, vx, vy]` on (r, theta, v_r), `sa` 10; R per detection from `snr_db`;
    gate 11.34
  - Done: tests (l)-(o) pass or are resolved via G7 10; `theta0` fake targets on the PPI
  - Gaps found 2026-11-12 (G7 9.1, 9.2):
  - [ ] Stop-coast: the MTI notch is on radial speed. Zero the velocity only when
        `hypot(vx, vy)` is under one bin; otherwise coast on the velocity
  - [ ] `theta` arrives in degrees; clamp `cos(theta)` at 0.5 in sigma_theta
  - [ ] `TRACK_STD_THETA_MIN_DEG` (0.5) as a config field: L6b tunes it, expect 1-2 deg
  - [ ] Run (l)-(o) at the recorded rates (~23 CPIs/s TDM, ~14 triangle + TDM), (o) with
        MTI on and off
- [ ] L6 - real targets (G7 12)
  - L6a, K6 recordings: approaching car v < 0; tune `sa`, `sr`, `sv` by NIS per class;
    spurs never confirm; a walker's range wander (the L2 fail floor). Fabric recordings
    only: software mode runs at ~4 CPIs/s
  - L6b, K11 recordings: walked line and a car traced on the PPI; NEES/NIS sane
  - Expect, and count instead of fixing (G7 12): multipath ghosts confirm (they obey
    dr/dt = v); fades at fixed ranges from the ground bounce (58, 29, 19 m for 1.5 m and
    1.0 m heights); slow tracks on vegetation
  - [ ] Check confirmation and ID hold on K6's padded recording (patch-class SNR)
- [ ] Docs from the 2026-11-13 review: G7 2 and 9.2 rates (recorded 21-25 CPIs/s TDM,
      13.7 triangle + TDM); G7 6.5 (fake targets give NIS ~0, L3 can't check `sr` / `sv`);
      duplicate row in G7 6.7; `common/tracking.py` into a rule file and `CLAUDE.md`
      tree; `tests/test_replay_tracks` is in `CLAUDE.md` but not in git

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

- [ ] `scripts/antenna_design/eps_r_sweep.md`: generated; let `sweep_eps_r.py` print the
      table and keep only the PNG.