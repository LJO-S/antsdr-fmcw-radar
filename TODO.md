# TODO

---------------------------------------------------------------
Ludvig's DO-NOT-FORGET:
- Fix the offline and online tests if they fail
---------------------------------------------------------------


Roadmap for the FMCW radar. Carrier is **5.8 GHz** (cheap WiFi/FPV hardware).

**This file tracks unfinished work only.** Completed parts get one line each and
their design conclusions move to `CLAUDE.md`, which is the maintained map - don't
grow narrative back in here as parts close.

Current state (2026-09-16): **Part I is the active track** (HDL offload). I4 fabric
dechirp closed on hardware 2026-09-10 (dechirp delay 34 in digital loopback, 61 on
cable); I4b `fabric_ctl.py` + worker wiring and I4c fake targets on the IF stream
landed 2026-09-13 and were confirmed on hardware 2026-09-16. Next is **I5
decimation** - `docs/AntSDR_Phase5_IF_Decimation_Guide.md` is the spec (rewritten
2026-09-16: three own halfband stages, not the fir_compiler IP and not a CIC).
Step 0 is done; next is importing the filter sources from
`~/work/projects/fpga/fpga-filters`.
Part G (antennas) is the next RF step. Part H (PA/LNA/BPF) stays deferred behind G.
Part J (synthetic wideband) blocks nothing and is testable in loopback today.

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
      interferometry/angle idea - note only, not a task. Prerequisite: the board runs
      an **AD9364** firmware image (device tree says `ad9361-phy,model: ad9364`,
      `adi,2rx-2tx-mode-enable: 0`, and `cf-ad9361-lpc` exposes one I/Q pair), so the
      second channel needs a DT/firmware rebuild first. The silicon itself is an AD9361
      - do not read the iio_info dump as "the hardware cannot do it".

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

## Part I - HDL offload (active track)

Reframed 2026-07-23: developing the fabric path is digital-domain work (simulation,
ramp tests, digital loopback) and runs in parallel with the RF parts; *switching the
radar over* to it still waits for Parts F+G proven on real RF. The target
architecture (fabric NCO + fabric dechirp, Ethernet carries IF, frame sync deleted,
`dsp.py` = golden model, bypass bits default to today's behaviour) and every
hardware-proven design conclusion (FMC3 register map, enable/disable order, DMA sync
latch, decimator control, loopback delay) live in `CLAUDE.md` under "Firmware / HDL
track". Guides: `docs/AntSDR_Phase5_IF_Decimation_Guide.md` (I5, current),
`docs/archive/` (Phases 1-4; Phase 4 Section 4 is still the NCO derivation).

Ladder (each rung proven before the next; sim first):

- [X] I1 - AXI-Lite register bank on `rx_tap` (2026-07-27): MAGIC/CTRL/SCRATCH/COUNT,
      VUnit tb, 2-flop + gray CDC, UIO + `src/c/rxtap` peek/poke, ramp toggled live.
- [X] I2 - chirp NCO in fabric (2026-08-31): bit-exact vs the fixed-point model in
      sim; sawtooth + triangle live-reconfigured from a shell and seen on the GUI
      spectrogram; CHIRP_COUNT delta/s = PRF on hardware.
- [-] I3 - TX mux + determinism proof: SKIPPED. `estimate_chirp_offset` jumps
      run-to-run on BOTH DMA and fabric-NCO TX, so the jitter is in the RX capture/DMA
      path, not TX generation. The TX mux itself (CTRL.tx_src) was absorbed into I4.
- [X] I4 - fabric dechirp (2026-09-10): `mixer_dechirp` (delay line + complex mult +
      conj + Q15 saturation) bit-exact vs the Python reference; wired into `fmcw_core`
      (MAGIC FMC3, IF_SEL @0x2C, DECHIRP_DELAY @0x24, STATUS @0x34); end-to-end
      dechirp tb; Python IF-mode bypass (`FABRIC_DECHIRP_EN`); the sync_src+IF_SEL
      capture hang found, root-caused (1-sample sync pulse vs 2-sample cpack beats)
      and fixed with a level-held `o_dma_sync`, confirmed A/B on hardware. Exit
      passed in digital loopback: fabric RD map matches the software-dechirp
      baseline (`scripts/bringup/dechirp_verify.py`), **loopback dechirp delay = 34
      samples**. Real-RF constant still to be measured (Part G).
- [X] I4b - `online/fabric_ctl.py` (2026-09-13): `register_image` is the only encoder,
      `FabricCtl` owns sequencing over an injectable transport (`SshDevmem`, one ssh
      call per sequence), enable/disable order unit-tested through a recording
      transport, `dechirp_verify.py` rewritten on top of it, worker wiring in
      `app.py` (`fabric_up`/`fabric_down` around every start/close, incl.
      reconfigure and revert), `sdr.start()` pins the `cf-ad9361-lpc` rate,
      config default `FABRIC_DECHIRP_EN = True`. Conclusions in `CLAUDE.md`
      ("Online app architecture" + "Fabric IF mode on the Python side").
- [X] I4c - fabric-mode usability. Done 2026-09-13: fake targets on the IF stream
      (`TargetSim.apply_if`), Signals tab hides the IF plot in fabric mode. Offline
      checks are in `online/test_target_sim.py`. Confirmed on hardware 2026-09-16
      (I5 step 0): a fake target moves in fabric mode, and in triangle mode it
      appears in both maps as `kind="both"`.

- [ ] I5 - decimation to IF rate. Spec: `docs/AntSDR_Phase5_IF_Decimation_Guide.md`
      ("G" below = its section). Decisions (2026-09-16): operational range 800 m at
      56 MHz / 100 us; one fixed divide-by-8 as **own HDL inside `fmcw_core`** -
      **three cascaded decimate-by-2 halfband stages** (7 / 11 / 27 taps, 2 / 3 / 7
      multiplies per channel, 24 DSPs for I+Q), each restarting its decimation
      phase on the chirp-leg tag so every chirp parameter stays free and each leg
      yields `SWEEP_LEN // 8` samples; no block-design change and no new
      `fmcw_core` ports; DECIMATE_SEL live select; MAGIC FMC4; FS stays 56.6;
      `OP_RANGE_FACTOR` -> `MAX_RANGE_M` + derived effective range; `SWEEP_LEN`
      rounded to a multiple of the ratio. Chain: **0.04 dB** droop at the band
      edge, **48.3 dB** alias rejection.

      Two earlier plans rejected, recorded so they are not re-proposed. The
      fir_compiler IP: ~16 DSPs, encrypted model Questa FSE cannot run, no
      per-chirp phase restart. CIC-by-4 plus one halfband: 12 DSPs and zero for
      the CIC, but a CIC is only valid on a uniform decimation grid, so a per-leg
      phase restart also has to clear its integrator and comb registers - miss that
      and the output diverges past the 24-bit range (~4 wrapped full-scale samples
      per leg, once per chirp), and it only shows when `SWEEP_LEN mod 4 != 0`, which
      today's 5660 is not. Plus 2.2 dB of droop. The all-halfband chain costs 12
      more DSPs out of ~185 free and deletes both problems.

      **The HDL is copied, not written.** `~/work/projects/fpga/fpga-filters` has
      `halfband_decimate` (+ `_stage`, `_pkg`), which already builds
      `log2(G_MULTIRATE_FACTOR)` stages from a generate loop - `= 8` is the whole
      chain, and its own tb runs `M = 32`. Fork it for the two things it lacks: a
      `restart` input (one flip-flop, `r_sel`) and a 2-bit tag passthrough. Its
      arithmetic already matches the guide: accumulate in
      `G_DATA_WIDTH + G_COEFF_WIDTH + ceil(log2(U))` bits, then
      `(acc_upper >> 15) + (acc_lower >> 1)` into a saturate-and-clip stage.
      **Two independent floors - the model must do the same** (G2.3).

      Coefficients: **two tools, clean split.** The filters repo supplies the RTL
      and the geometry - set `M = 8`, `FS = 56.6e6`, `FPASS = 2.83e6`,
      `atten_db = 55` and run it (done 2026-09-17, 3 s) to get the package,
      `C_NUM_TAPS_UPPER = (4, 6, 14)` / `C_NUM_TAPS_LOWER = (1, 2, 6)`, i.e.
      7 / 11 / 27 taps. `atten_db` only picks tap counts and then stops mattering;
      the coefficients it emits are **not** the ones we ship.

      This repo designs the coefficients, because the structure makes an ordinary
      equiripple halfband unable to have unity DC gain. The centre tap is a
      hardcoded `>> 1` and every other tap sits at an odd offset, so
      `A(w) + A(pi - w) = 1` identically, hence `A(0) = 1 - A(fs/2)`: the DC-gain
      error *is* the stopband ripple, with opposite sign (measured residuals
      +6 / -12 / -88 counts = -74.8 / -68.7 / -51.4 dB, which are exactly those
      designs' stopbands). Unity gain means forcing a null at Nyquist, which
      costs alternations. **Constrain the design, do not patch the taps**:
      constrained minimax costs 0.22 dB (-51.64 -> -51.42 dB), patching a
      finished design costs 3.3-5.9 dB. The solve lives in the filters repo
      (`scripts/model/unity_dc_halfband.py`, scipy linprog/HiGHS, deterministic)
      behind `Halfband_filter(a_unity_dc_gain=...)`, default off; `run.py`'s
      halfband-decimate cfg sets it, so the generator's output is directly
      shippable and there is **one** coefficient designer. Added and verified
      2026-09-18: all 7 tests in that repo pass, emitted cascade -51.42 dB.
      Run once, check in, never regenerate at build time. Stage-3 dial: 23 taps
      -> -45.2 dB / 22 DSPs, **27 -> -51.4 / 24**, 31 -> -56.9 / 26.

      Work sessions, in order. Each stands alone; read only the G section named.

      - [X] **0. Hardware check of I4c** (10 min, board on, no code). Done
            2026-09-16: fake target moves in fabric mode; with `TRIANGLE_EN` it
            lands in both maps as `kind="both"`.

      - [X] **1. Import + coefficients** (no board; G4.1). Mostly done 2026-09-18:
            `halfband_decimate.vhd`, `halfband_decimate_stage.vhd`,
            `halfband_decimate_pkg.vhd` and the three `HBF_16_{0,1,2}.txt` are
            vendored into `projects/e200/src/decimate/` with a `README.md`
            recording the source commit (`1eacfe1`). 
            Remaining: add the files to `test/run.py`'s source list, and
            **replace the checked-in `HBF_16_*.txt` with the constrained set**
            (the vendored ones are the old patched -48.3 dB taps; the generator
            now emits the -51.4 dB set directly). `scripts/force_hb_sums.py` and
            the paragraph about it in `src/decimate/README.md` both go. Done when `python run.py --compile` is clean with the VHDL
            unmodified, the package reads `(4, 6, 14)` / `(1, 2, 6)`, and the three
            tap sets each sum to 16384.

      - [X] **2. Model + tests** (Python, no board; G5, G2.3). New
            `projects/e200/scripts/decimate_reference.py`, built on the filters repo's
            `scripts/model/halfband_filter.py`: `load_taps()` reads the **checked-in
            init files** (never designs its own - model and RTL must share one tap
            set), `check_taps()` asserts symmetry / odd-taps-zero / sum 16384 and
            the cascade response at import, `hb_stage_golden()` (integer FIR,
            decimate 2, restart resets the phase only, `(acc_upper >> 15) +
            (acc_lower >> 1)` with two separate floors, clip), `decimate_golden()`
            chaining the three. Tests (a)-(e) as `__main__` per G5 - note (b) is
            "DC out within **one** count and exactly equal to the model", not
            "exact", because of the two floors. Done when they pass.

      - [X] **3. Fork `halfband_decimate` -> `halfband_decimate` + tb** (HDL + VUnit;
            G2.3, G2.4, G3.4). `G_MULTIRATE_FACTOR = 8`, three stages from the
            generate loop. Add `i_restart` resetting `r_sel` only - delay lines keep
            their history - and a 2-bit `i_tag`/`o_tag` per stage, so stage k+1's
            restart is stage k's tagged output. Two instances for I/Q. Confirm
            `p_saturate_and_ovf` clips rather than wraps. `tb_halfband_decimate`, per
            stage and as the cascade: random + full-scale stimulus, valid gaps,
            restart at a spacing not a multiple of 8, and a sign-matched full-scale
            burst `x[n] = 32767 * sign(h[K-n])` for the saturation (DC does not
            clip, gain is 1.0). Bit-exact vs `decimate_golden`, and `N // 8` outputs
            per leg. Done when green.

      - [X] **4. Core integration** (2026-09-22; HDL + VUnit; G3.1-G3.4). Tag lane:
            `chirp_generator.o_new_leg`, 2-bit tag through `delay_line` and
            `mixer_dechirp` (same delay as valid). Chain after the ADC output mux,
            bypassed when `decimate_sel=0`; sync latch set from stage 3's period tag
            when `if_sel=1`; ramp counter resets on the IF period tag when
            `nco_en`; DECIMATE_SEL live (2-flop sync, false path);
            MAGIC `0x464D4334`; `set_max_delay` on `r_dechirp_dly -> delay_line`;
            Makefile `M_DEPS` (every `src/decimate/` file incl. the three init files,
            plus the 4 missing today). No new `fmcw_core` ports. `tb_fmcw_core`:
            registers; 32 decimated samples per leg at `chirp_len=262` (sawtooth +
            triangle), 262 when off - 262 not 260, so the restart has an irregular
            gap to absorb; chain bit-exact vs `decimate_golden(mixer_golden())`;
            `period-framing` x `decimate_sel=1`; ramp reset. Done when all green.
            **STATUS bit1 skipped** (2026-09-22): DECIMATE_SEL readback is the
            same round trip, so the mirror bit earns nothing on either side.
            `STATUS_DECIMATE_ACTIVE` drops out of session 5 with it.
            Closed at **45/45 VUnit, 7/7 offline**. What the session actually
            cost, beyond the plan: the DMA sync was tapped one register too
            late, so the window opened a full IF sample after the period-tagged
            sample at `decimate_sel=1`. Fixed by driving the latch from
            `w_out_tag`, the combinational tag feeding the output register -
            lag 0 in both modes, so `FABRIC_FRAME_TRIM` is 0 by construction
            and session 8 is a confirmation. That fix is only safe because
            `mixer_dechirp` now gates its tag with its valid; ungated, the
            held-level tag rose a cycle early and tapping earlier as well would
            fire a beat early. Three tests were missing and now exist:
            `ramp-reset` (the first config ever to set `ramp_en|nco_en`
            together - `w_ramp_restart` had never fired in sim), four
            `period-framing` configs at `decimate_sel=1`, and `decimate-legs`
            swept over `chirp_len` 256/257/262/263 so the restart is graded at
            four `mod 8` residues instead of one. The `mod 8 = 0` case is the
            one that matters: it is the only length where a restart that did
            nothing would still produce the right count.

      - [X] **5. Host side** (2026-09-23; Python, no board; G6). `config.py`: delete
            `OP_RANGE_FACTOR`; add `MAX_RANGE_M`=800, `FABRIC_DECIMATE` {1,8},
            `FABRIC_FRAME_TRIM`=0; properties `FS_IF`, `SWEEP_LEN` (multiple of
            `FABRIC_DECIMATE`), `T_EFF = SWEEP_LEN / FS`, `N_IF`, `EFFECTIVE_RANGE`,
            `MAX_RANGE=min(...)`; every range axis off `T_EFF`, not `CHIRP_DUR_S`.
            `fabric_regs.py`: MAGIC FMC4, `DECIMATE_CODE`,
            image adds DECIMATE_SEL, `chirp_ftw` takes the rounded sweep length and
            **recomputes the slope from it** (else B shrinks). `fabric_ctl.py`:
            DECIMATE_SEL after `nco_en` / before IF_SEL on enable, between IF_SEL and
            CTRL on disable; `test_fabric_ctl.py` asserts the
            positions. `dsp.py`: `CPIContext.N_if_samples`/`fs_if`, `process_cpi` on
            them, `spectrogram(..., a_fs=)`. `sdr.py`: buffer from `N_IF`; FS
            readback warning. `capture.py`: trim slice. `target_sim.apply_if`:
            FS_IF/N_IF. GUI derived table: FS_IF, N_IF, effective range (flag if <
            MAX_RANGE_M). Done when `test_fabric_ctl`, `test_target_sim`,
            `test_soft_model` pass and the GUI still runs in software mode with the
            usual RD map. Done: `FABRIC_DECIMATE` became `FABRIC_DECIM_EN` (ratio
            is fixed in HDL). Software mode's range axis now stops at 800 m.

      - [ ] **6. Build** (Vivado; G3.3). No block-design change. Full image, flash.
            Done when `/dev/uio0` exists, MAGIC reads FMC4, DECIMATE_SEL reads
            back what was written, and the app runs at `FABRIC_DECIM_EN=False` as
            before.

      - [ ] **7. Ramp** (board; G7.1). CTRL `ramp_en|nco_en`, DECIMATE_SEL=1, IF_SEL=0,
            one block: step exactly 8 (mod 2^16), drop every `SWEEP_LEN // 8`,
            `np.array_equal` vs `decimate_golden` test (c). A step of 8, 8, 8, 7 is a
            tap sum that is not 16384. Done when bit-exact.

      - [ ] **8. Trim** (board; G7.2). Same with `sync_src=1`: first drop index ->
            `FABRIC_FRAME_TRIM` (expect 0 or 1); re-run, drop at index 0.

      - [ ] **9. A/B** (board; G7.3). `dechirp_verify.py --decimate 1` then `--decimate 8`,
            fake targets 100/300/500 m, +-20 m/s. Below 750 m: same bins, maps
            within 1 dB above -60 dB, same leakage line. No droop to allow for - a
            tilt toward the far end of the range axis is a real finding.

      - [ ] **10. Config freedom** (board, GUI; G7.4). RE-CONFIGURE to T=50 us, then
            B=25 MHz, then triangle. No hang; effective range 425 / 1700 m; fake
            targets follow (one beyond the range disappears).

      - [ ] **11. Exit** (board, GUI; G7.5). CPIs per second for a minute at /8;
            expect >= 10 fps. Write the number here. Gitlink bump bottom-up, collapse
            I5 to one line, conclusions to `CLAUDE.md`.

      - [ ] **12. Reclaim** (own build, after 9; G4.2). Delete `rx_fir_decimator`,
            `decim_slice`, `tx_fir_interpolator`, `interp_slice`; wire the core
            straight to `axi_ad9361` / `tx_upack`. linux submodule (branch first):
            drop the two `adi,axi-*-core-available` dtsi properties. `sdr.py`: remove
            the `cf-ad9361-lpc` rate pin. Full image. Done when step 9 still passes
            and the utilization report shows ~50 DSPs (75 today - ~50 reclaimed
            + 24 ours).

- [ ] I6 - integration, now small: `sync_src=1` is already the fabric default and
      `FABRIC_FRAME_TRIM` replaces frame sync with a constant. Left: delete
      `estimate_chirp_offset`/`frame_sync_linear` from the *fabric* path (they stay
      for software mode); GUI bypass bits (already auto-generated from config
      metadata); `SDR_RX_MARGIN_PERIODS` stays (the trim slice needs it).
      Daily-driver cutover to fabric mode still gated on Parts F+G on real RF.

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
