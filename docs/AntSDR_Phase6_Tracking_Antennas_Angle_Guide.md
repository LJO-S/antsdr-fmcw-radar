# AntSDR E200 - Phase 6: tracking, RF bench, patch antennas, 2T1R angle (Part K)

Follows Phase 5 (archived). Assumes: Parts A-G and I done. The fabric chirp NCO,
dechirp and /8 decimation are proven in digital loopback (MAGIC FMC4, up to ~30
fps); Part G passed in software mode with the COTS antennas (sector + grid, ~1.5 m
apart, all defaults). The fabric path has not yet seen real RF.

Same contract as before: this guide specifies, you write the code. Register map,
sequencing rules and conventions live in `CLAUDE.md`; this guide only states what
is new.

Rewritten 2026-09-28. Angle now comes from two switched TX antennas (2T1R), not two
receivers, and the phase gained an RF bench lane (VNA, soldering). Section 1.2 says
why. K0 (the 2R2T boot check) is done; its results are Appendix B.

## 1. What and why

This phase is where the project turns from software and FPGA work into RF
engineering: a VNA, soldering, an RF component chosen and measured, antennas
designed and characterized. Tracking runs first because it needs nothing new and
turns detections into tracks.

| Work | Payoff | Gated by |
|---|---|---|
| Tracking (K1-K3) | Stable IDs, smoothed v, fewer one-CPI false alarms | Nothing |
| Field day 1 (K6) | Fabric mode on real RF, first IQ recordings | K2, weather |
| RF bench (K4, K5) | VNA, S-parameters, soldering: the tools every later step uses | A VNA and a soldering kit |
| TX switch (K7) | An RF component chosen, measured and integrated; the 2T1R hardware | K4, K5 |
| Patch antennas (K8, K9) | Antenna design, simulation and measurement; a compact rig | K4, K5, PCB lead time |
| Angle and x-y tracking (K10, K11) | Targets on a map | K7, K9 |
| Fabric FFT/CFAR | Latency, detections-only link | Decision gate (Section 11) |

```
software lane:  K1 profile -> K2 recorder -> K3 tracker (r, v)
                                  \-> K6 field day 1 (COTS antennas)
RF lane:        K4 VNA on owned gear -> K5 soldering + J30 header -> K7 TX switch
                                                     \-> K8 single patch -> K9 columns
then:           K10 angle on host -> K11 tracker (x, y) + field day 2
gate:           Section 11, fabric FFT/CFAR
```

Order the kit (K4, K5) while K1-K3 run: shipping takes weeks. Order the patch PCBs
(K8) as soon as the simulation is done, and do K7 while they ship. Each lane opens
with its reading in Section 13.

Why the fabric FFT goes last: the ~30 fps falls as detections rise, so the host
bottleneck sits after the FFT (CFAR/NMS/sub-bin/pairing or the GUI scatter). A
128 x 707 FFT is a few ms in numpy. Moving the FFT into fabric alone would not raise
the frame rate.

### 1.1 How angle works here: 2T1R time-division MIMO

One receiver, two TX antennas lambda/2 apart, and an RF switch after TX1 that
alternates between them every chirp: chirp A leaves antenna A, chirp B antenna B.
For a target at angle theta, the B echoes carry an extra phase `pi sin(theta)`
relative to the A echoes. Two TX positions and one RX form a two-element *virtual
array*: the same phase measurement a 1T2R radar makes with two receivers. This is
time-division MIMO, the scheme TI's mmWave radars use.

What it costs:

- Each antenna gets every second chirp, so the slow-time interval is `T_rep = 2T`,
  as in triangle mode: unambiguous velocity 129 -> 64 m/s. With `CHIRP_REPS` chirps
  per antenna the CPI doubles to 25.6 ms and velocity bins shrink to 1.01 m/s.
- A moving target's phase also advances between an A chirp and the next B chirp.
  The host removes that term with the measured Doppler (Section 10.1).
- The fabric drives the switch from the NCO's chirp boundary, so TDM needs fabric
  mode. Software mode does not know where a chirp starts.

### 1.2 Why not two receivers (2R2T)

K0 proved 2R2T boots and the fabric is transparent to it, but the E200 connects the
AD9361 over its CMOS interface (1.8 V bank, `CMOS_OR_LVDS_N 1`): at most 61.44 MHz
of data clock, shared by both RX channels. So 2R2T caps FS at **30.72 MSPS**: a
~25 MHz chirp and ~6 m range bins instead of 3 m. LVDS needs a 2.5 V bank - a
board change. Sources in Appendix B.

| | 2R2T | 2T1R, switched TX |
|---|---|---|
| Range bins | ~6 m | 3 m |
| FPGA work | second mixer + decimator, equal latency, +~52 DSPs | one output pin, sync per chirp pair |
| Hardware | 2 IPEX pigtails (RX2/TX2 are IPEX, not SMA) | switch module, soldered J30 header |
| Host | simultaneous channels, no motion term | Doppler correction, v_max halves |
| RF content | little: mostly FPGA | an RF component end to end |

2T1R wins on resolution and on RF content, which is what this phase is for. 2R2T
stays documented as the fallback.

### 1.3 The link-budget consequence of patches

Under the 25 mW SRD EIRP cap, **TX antenna gain and TX-side loss are free**: a
lower-gain TX antenna or the switch's insertion loss just takes more TX port power,
and the E200 has headroom (~+6.5 dBm vs the ~-2 dBm allowed with the 16 dBi sector).
Only one TX antenna radiates at a time, so the cap applies per antenna. **RX gain is
the cost**: range goes as `G_rx^(1/4)`.

| RX antenna | vs the 30 dBi grid | Range factor |
|---|---|---|
| 1x8 column, 15 dBi (Rogers) | -15 dB | 0.42 |
| 1x8 column, 13 dBi (FR4) | -17 dB | 0.38 |

So roughly 40 % of the grid's range: a person at a few hundred metres, cars further,
inside the 800 m decimation envelope. The beams get wide (~70-80 deg in azimuth, vs
a few degrees for the grid), so more ground clutter enters and MTI will matter more
than it did in Part G.

---

## 2. K1 - profile the host (no board)

Measured: up to ~30 CPIs/s at /8, falling with detection count. The limits
elsewhere are far above that: a CPI is 12.8 ms (up to ~78 CPIs/s back to back), and
365 kB per CPI over ~118 MB/s is ~320 CPIs/s.

- Bench script: synthesize an IF block with K beat tones plus noise (reuse
  `TargetSim.apply_if`) for K = 0, 5, 20, 50. Time each stage of `process_cpi`
  with `perf_counter`, median over ~50 runs: range FFT, Doppler FFT, close-in mask,
  CFAR, NMS, sub-bin refine, pairing, target-dict building.
- In the app, time the GUI slot too (`setImage` + scatter `setData`). A DEBUG log
  line per CPI is enough.
- Stages that grow with K are the suspects. Per-detection Python loops get
  vectorized; the scatter redraw can be throttled like the spectrogram.

Done when you know which stage grows with detections, and it is fixed or
knowingly accepted. Write the ms-per-stage table into `TODO.md`: K3 and K10 add
per-CPI work on top of it.

---

## 3. K2 - recorder and replay (no board)

Part G produced video only, so nothing from it can be replayed. From now on every
field day records IQ. The recordings drive the tracker (K3) and the angle work (K10)
at a desk, against real clutter.

**Format**, one directory per session:

- `session.json`: `dataclasses.asdict(cfg)`, MAGIC, `git describe` of this repo,
  wall-clock start, free-text notes.
- `blocks.bin`: raw capture blocks appended exactly as the DMA delivered them
  (int16, interleaved, before trim and before `TargetSim`).
- `index.csv`: one line per block, `monotonic_t, byte_offset, n_bytes`, flushed
  per line so a crash loses at most one block.
- `detections.jsonl`: the live detection list per CPI. It makes replay
  self-checking.

**Hook**: record in `capture_rx_data`, on the raw block, before `TargetSim`. That
needs the int16 block from `sdr.py` (return the raw view and normalize after), not
the normalized complex64.

**Ownership**: worker-owned, per the app invariants. A GUI checkbox sends
`record_changed(bool)` like MTI does; the worker opens and closes sessions between
CPIs. RE-CONFIGURE closes the session and opens a new one, since one session has one
config.

**Disk**: /8 is ~11 MB/s at 30 fps (~0.65 GB/min); software mode is similar
(bigger blocks, fewer per second). Size the field laptop's disk for it.

**Replay** (`offline/replay.py`): rebuild `RadarConfig(**fields)` (properties
recompute), `build_cpi_context`, then run each block through the same path as the
worker (fabric trim, or software frame sync + mix) into `process_cpi` and the
tracker. Timestamps come from `index.csv`, so replay is deterministic and can run
faster than real time.

Done when a 60 s loopback session with loopback noise off (the added noise is
random) replays to the identical per-CPI detection list stored in
`detections.jsonl`.

---

## 4. K3 - tracker in range and velocity (no board)

Read first: 13.3.

### 4.1 Conventions - check these before writing F

- **Sign**: `TargetSim` moves `r = r0 + v0*t`, and `test_target_sim.py` asserts
  that `process_cpi` reports `v0` with that sign. So **v > 0 is receding**, and
  `dr/dt = +v`. Confirm on the first real recording: an approaching car must read
  v < 0.
- **Time**: `dt` comes from the CPI timestamps (monotonic, at capture return),
  never from a nominal frame period. The frame rate varies with detection count.

### 4.2 Model

Constant-velocity Kalman filter per track. State `x = [r, v]`, measurement
`z = [r, v]`: the radar measures both directly, so `H = I`.

```
F = [[1, dt], [0, 1]]
Q = sa^2 * [[dt^4/4, dt^3/2], [dt^3/2, dt^2]]      # white-noise acceleration
R = diag(sr^2, sv^2)
```

Defaults, all config fields: `sa` = 3 m/s^2, `sr` = 0.7 m (sub-bin refined, bins
are 3.0 m), `sv` = 0.5 m/s (bins are 2.02 m/s).

Because v is measured, a track can start from a single detection with `P0 = R`. No
two-point initialization is needed.

### 4.3 Association and track management

- Gate: Mahalanobis `d^2 = y' S^-1 y < 9.21` (chi-square, 2 dof, 99 %).
- Global nearest neighbour: `scipy.optimize.linear_sum_assignment` on `d^2`, with
  out-of-gate pairs set to a large cost. Unassigned detections start tentative
  tracks.
- Confirm after M of N CPIs (default 3 of 4). Delete by **time**, not by CPI count:
  a confirmed track after 0.5 s without an update, a tentative one after 2 misses.
- `TRACK_MIN_SPEED` (default 0 = off): minimum |v| to start a track, for scenes
  full of parked cars when MTI is off.
- Velocity ambiguity is not an issue: `MAX_VELOCITY` is 129 m/s (64 m/s in
  triangle and TDM).
- Triangle mode: accept every `kind`. Weighting `R` by kind is a later refinement.

### 4.4 Placement

- `common/tracking.py`: pure numpy/scipy, no Qt. `Tracker(cfg).update(detections,
  t) -> list[Track]`. `Track` = id, state, covariance, status, hits, misses, and a
  short history of `(t, r)`.
- **Keep the motion/measurement model behind one small interface** (predict,
  predicted measurement, Jacobian, R). K11 swaps in a 2D model with a nonlinear
  measurement; association and track management must not change.
- The worker owns the tracker (state across CPIs), calls it right after
  `process_rx_data`, and rebuilds it on RE-CONFIGURE. Tracks go to the GUI on a new
  `tracks` signal, so `results` stays unchanged.
- GUI: tracks on the existing detections scatter, as labelled markers with a short
  tail.
- Config: `TRACK_EN`, `TRACK_SIGMA_A`, `TRACK_SIGMA_R`, `TRACK_SIGMA_V`,
  `TRACK_CONFIRM_M/N`, `TRACK_DELETE_S`, `TRACK_MIN_SPEED`, group `tracking`. The
  GUI rows come for free.

### 4.5 Tests (`common/test_tracking.py`, `__main__` asserts)

Synthesize detection streams from ground-truth kinematics: measurement noise at
(`sr`, `sv`), Pd = 0.9, ~2 false alarms per CPI uniform over the map, `dt` jittered
over 20-50 ms.

- (a) One target: one confirmed track, same ID for 20 s, RMS range error below `sr`.
- (b) Two targets crossing in range with different v: IDs do not swap.
- (c) False alarms only: no confirmed track, or fewer than one per minute.
- (d) Target vanishes: its track is deleted within `TRACK_DELETE_S`.
- (e) Fixed vs jittered `dt`: same tracks.

Then live: loopback fake targets give one stable track each. Then the K6
recordings.

Done when (a)-(e) pass, fake targets track in the live app, and the fps cost is
measured against the K1 table.

---

## 5. K4 - RF bench kit and VNA basics (no soldering)

Read first: 13.1.

The VNA is the instrument an RF engineer uses most, and every later step uses it.
Start on gear you already own, where datasheets give you something to check against.

### 5.1 Kit

- A VNA covering >= 6 GHz (LiteVNA-64 class; a NanoVNA-H4 stops at 1.5 GHz), with
  its SMA calibration standards.
- The soldering kit for K5 (Appendix A). Order both while K1-K3 run.

### 5.2 Measurements on owned gear

Calibrate at the ends of the test cables over 4.5-6.3 GHz (a LiteVNA-64 stops at
6.3 GHz; the VBFZ passband starts at 4.9 GHz). For every item, **write
your prediction down first** (datasheet or hand calculation), then measure, then
explain the difference. The prediction is where the learning is.

- **VNA floor**: S21 with both ports terminated. It bounds every isolation reading;
  trust a reading only ~10 dB above it.
- **HF240 cables**: S21 vs 0.676 dB/m plus ~0.1-0.2 dB per connector; S11. The
  electrical length from the S21 phase slope (group delay) gives the velocity factor.
- **Wuerth RG316 jumper**: the same, ~3.9 dB/m.
- **VAT-15A+ pads**: S21 ~ -15 dB and flat; S11.
- **VBFZ-5500-S+ filters**: passband 4.9-6.2 GHz, IL 1.26 dB typ at 5.8 GHz
  (datasheet in `docs/datasheets/`).
- **Molex terminations**: return loss at 5.8 GHz.
- **Sector and grid antennas**: S11 over 5.7-5.9 GHz. Point them at open sky, away
  from walls; anything in the near field changes the reading.
- **Smith chart**: an open-ended cable's S11 turns around the chart as frequency
  rises. Predict the rate from its electrical length, then check.

Done when every item has prediction, measurement and explanation in a table
(`hardware/bench_measurements.md`).

---

## 6. K5 - soldering and the J30 GPIO header

Read first: 13.1 (soldering).

The E200's 3.3 V GPIO sits on **J30**, a 10-pin 2.54 mm header footprint that is not
fitted (schematic `ANT-E200_Public.pdf`, p.10): pin 1 VCC_3V3, pin 2 GND, pins 3-10
GPIO_00-07, ESD-protected. In the HDL these are `GPIOB[7:0]` (bank 13, LVCMOS33),
today routed to the PS GPIO through `ad_iobuf` (EMIO 35-42, Linux sysfs gpio
995-1002). Upstream MicroPhase constraints reuse Y9/Y6 (`GPIOB[7:6]`) as a UART;
this project's `system_constr.xdc` maps all eight to `GPIOB`.

1. **Practice first**: 20 or more through-hole joints on a scrap board or a cheap
   practice kit, until they come out shiny and concave.
2. **Fit the header**: board unpowered, ESD care, a straight 10-pin header in J30.
3. **Map the pins from Linux**, before any HDL: `echo 995 > /sys/class/gpio/export`,
   `direction` = `out`, toggle `value`, find each line on J30 with a multimeter. The
   XDC order `GPIOB[0..7]` = V5, U7, V7, T9, U10, Y7, Y6, Y9 probably matches
   GPIO_00-07; confirm it.

Done when every J30 GPIO pin is mapped and toggles 0 / 3.3 V.

---

## 7. K6 - field day 1: fabric on real RF (board, weather)

Needs K2. COTS antennas only: they are known-good RF, and the rule stands - never
debug new RF and new HDL at once.

1. **COTS, software mode**, recording on: a static scene, then movers. Put a corner
   reflector at a tape- or laser-measured range. A trihedral with 30 cm edges is
   ~12.7 m^2 (`4 pi a^4 / (3 lambda^2)`). It is also the end-to-end range-axis check
   Part F skipped.
2. **Delay**: `dechirp_verify.py --no-loopback --sweep` (`SDR_LOOPBACK_EN = False`),
   starting the sweep at 0. Expect 61 +-1: 1.5 m of air is ~5 ns, ~0.3 samples.
3. **A/B**: `dechirp_verify.py --no-loopback --decimate 8`, software vs fabric /8.
   Compare only the static content (leakage, reflector, clutter); movers differ
   between the two phases.
4. **Fabric /8 in the app**, recording on: cars, people on foot, bikes, at several
   ranges, plus a quiet stretch.

Done when fabric /8 matches software mode on real RF and the delay is recorded.
Then flip the `FABRIC_DECHIRP_EN` default.

---

## 8. K7 - the TX switch

Read first: 13.1 (the datasheet exercise), 13.4.

### 8.1 The part

- **AliExpress "RF switch module SPDT 6 GHz", HMC8038 variant.** Chip datasheet,
  4-6 GHz: 0.9 dB IL typ (1.3 max), 51 dB isolation typ (40 min), 150 ns switching,
  one control line (VIH 1.15 V at VDD 3.3 V), single 3-5 V supply. SMA ports and a
  header are fitted, so it wires with Dupont leads. The same listing sells an HMC849
  variant (more IL, VIH 2.0 V, workable) and an **HMC349 variant (4 GHz max: wrong
  part)**. Buy two: clones vary.
- Unverified on the module: the header pinout, whether EN is tied low (it must be
  low), the real IL at 5.8 GHz on its FR4, whether the chip is genuine. 8.2 answers
  all four.
- Known-good fallback: Mini-Circuits ZFSWA2-63DR+ (DC-6 GHz, one CMOS line, 2.0 dB
  IL at 6 GHz; +V on a feed-through pin).
- Avoid parts that need two complementary control lines or a negative supply
  (PE42420, ADRF5020 and similar).
- Supply: J30 pin 1 (3.3 V) if the module accepts 3.3 V. Check its supply current
  first.

### 8.2 Characterize it on the VNA

Control pin tied to 3.3 V or GND through the module's jumper, one state at a time:

- IL, common -> A and common -> B, at 5.8 GHz.
- Isolation, common -> the off port. 40-50 dB can sit at a cheap VNA's floor:
  compare with the K4 floor measurement.
- S11 of each port, in both states.

Finite isolation means the off antenna radiates too, which bends the measured angle
by up to `asin(10^(-I/20))`: 1.8 deg at 30 dB, 0.6 deg at 40 dB.

### 8.3 HDL (MAGIC -> FMC5 = `0x464D4335`)

- New CTRL **bit6 `tdm_en`**. Like `triangle_en` it changes the period structure,
  so it takes effect on a COMMIT: write it in the pre-COMMIT CTRL word.
- **A period is a pair of chirps.** Reuse the triangle machinery in
  `chirp_generator`: with `tdm_en`, a period has two legs (leg 0 = A, leg 1 = B)
  with the **same** slope. The leg tag still fires every chirp, so the decimator
  restart is unchanged. The period tag - and with it the DMA sync and CHIRP_COUNT
  (5000/s) - fires once per pair. So every capture starts on an A chirp, and the
  host relies on row 0 being A.
- New output **`o_tx_sel`** = the current leg index, held 0 while `tdm_en` = 0.
  `system_bd.tcl`: a `create_bd_port` plus `ad_connect`. `system_top.v`: take the
  J30 pin chosen in K5 (say `GPIOB[0]`) out of the PS `ad_iobuf` and drive it from
  `tx_sel`. A 5 kHz square wave has no timing concern: false-path the port.
- `tdm_en` together with `triangle_en` is undefined in v1 (four legs):
  `register_image` raises.
- Timing: `tx_sel` flips at the NCO's leg boundary; the RF chirp leaves the AD9361
  a fraction of a microsecond later. The mismatch lands in the chirp-boundary
  transient that already exists (~13 IF samples at /8). Add a `TX_SEL_DELAY`
  register only if 8.4 shows it matters.
- Python: `TDM_EN` config field (requires `FABRIC_DECHIRP_EN`, excludes
  `TRIANGLE_EN`); `register_image` sets bit6; the capture holds `2 x CHIRP_REPS`
  rows, as in triangle mode.
- VUnit (`tb_fmcw_core`): `o_tx_sel` toggles once per chirp; the period tag and DMA
  sync fire on A legs only; with `tdm_en` = 0 everything is bit-identical to FMC4;
  decimate-legs still yields `SWEEP_LEN // 8` per leg.

### 8.4 Bench test in the radar (no antennas)

`SDR_LOOPBACK_EN = False`, fabric /8, `TDM_EN`. TX1 -> switch common; port A ->
>= 30 dB pad -> RX1; port B -> a 50 ohm termination. Per row of
`dechirp_verify.range_profile_db`:

- even rows (A) show the cable path at full level; odd rows (B) show it lower by
  the in-system isolation. Compare with 8.2.
- the loud row is row 0 in **every** capture: sync per pair works.
- swap A and B at the switch: the pattern flips.
- the first IF samples of each chirp: the switch transient stays inside the
  existing boundary transient.

Done when the VNA numbers are written down, VUnit is green, and the bench test
passes.

---

## 9. K8, K9 - patch antennas

Read first: 13.2.

### 9.1 Targets

- **Match**: S11 < -10 dB over 5.75-5.85 GHz (the chirp spans 5.775-5.825).
- **Gain**: >= 13 dBi per column on FR4.
- **Beam**: azimuth HPBW >= 60 deg, elevation ~12 deg.
- **Polarization**: linear, the same on TX and RX. When mixing with the COTS
  antennas, match their polarization.

### 9.2 Why columns, not square arrays

Angle comes from the phase difference between the two TX positions, and it is only
unambiguous for `|sin(theta)| < lambda / (2 d)`, with `d` the distance between the
two TX columns' phase centres.

| Centre spacing d | Unambiguous field of view |
|---|---|
| lambda/2 = 25.9 mm | +-90 deg |
| 0.6 lambda = 31 mm | +-56 deg |
| two 4x4 arrays side by side (~2.8 lambda) | +-10 deg, about the beam itself |

A series-fed **1x8 column** is ~16 mm wide, so two of them fit at lambda/2. They
give a narrow elevation beam, a wide azimuth beam, and an angle that is unambiguous
across it. This is the standard automotive FMCW layout.

### 9.3 K8 - single patch first

- Substrate: FR4, 1.6 mm, 2 layers. Nominal eps_r 4.4, but 4.2-4.6 in practice
  at 5.8 GHz, tan d ~0.02. That spread moves resonance by 100-250 MHz, more than
  the patch bandwidth, which is why a calibration spin comes first.
- Starting dimensions (transmission-line model, eps_r 4.4): W ~ 15.7 mm,
  L ~ 11.8 mm, inset feed from a 50 ohm line. A "1.6 mm" 2-layer board is ~1.5 mm
  of dielectric plus copper, so simulate h = 1.5 mm (line ~2.9 mm wide). Final
  numbers come from openEMS (`scripts/antenna_design/`): ~60 % radiation efficiency
  on FR4, ~5.7 dBi per patch.
- On the same panel: a plain 50 ohm through line (loss and eps_eff) next to the
  patch.
- Edge-mount SMAs rated well past 6 GHz, soldered with the K5 skills.
- Measure S11 on the VNA. Back out eps_r from the resonance shift:
  `eps_r_real ~ eps_r_sim * (f_sim / f_meas)^2`. Re-run the simulation with it
  before K9.
- Fallback: Rogers RO4003C, 0.508 mm (eps_r 3.38 +-0.05, tan d 0.0027), if FR4
  batch spread or loss is too much.

Done when the corrected simulation predicts the measured resonance within +-50 MHz.

### 9.4 K9 - columns

- Series-fed 1x8. Element pitch ~ one guided wavelength of the connecting line,
  ~28 mm on FR4 (0.55 lambda0 in free space, so no grating lobes). The column is
  ~23 cm long.
- Uniform amplitude first. A Dolph/Taylor taper through the patch widths comes
  second: elevation sidelobes are where ground clutter gets in.
- **TX board**: two columns, centre spacing lambda/2 = 25.9 mm (0.6 lambda if the
  coupling is worse than -15 dB). Identical feeds with equal line lengths to the
  SMAs, so the A-B phase offset is nearly constant; calibration takes the rest.
- **RX board**: one column.
- Expected: ~13 dBi on FR4, ~15 dBi on Rogers, azimuth HPBW ~70-80 deg, elevation
  ~11-12 deg.

### 9.5 K9 - bench measurements

- **S-parameters** (VNA from K4):
  - S11 of every column.
  - S21 between the two TX columns (coupling, target <= -15 dB).
  - S21 between the TX and RX boards vs spacing (isolation - this replaces Part G's
    skipped isolation item). Reference: the leakage the E200 sees with the COTS
    antennas at 1.5 m, measured once with the same RX gain.
- **Gain, two-antenna method with the E200**: the RX column and one TX column (same
  design) face to face at R >= 2 m (far field `2 D^2 / lambda` ~ 1.9 m for a 23 cm
  column).
  - Reference: TX cable -> known pad -> RX cable. Read the peak level in the range
    profile (`dechirp_verify.range_profile_db`: no close-in mask).
  - Then swap the pad for the two antennas, same RX gain:

    ```
    G_dBi = 0.5 * (level_ant - level_ref - pad_dB + FSPL(R))
    FSPL(R) = 20*log10(4*pi*R / lambda)      # 53.7 dB at 2 m, 57.3 dB at 3 m
    ```

  - The pad keeps RX below its +2.5 dBm absolute maximum; keep the TX low.
- **Pattern**: rotate the TX board on a marked turntable (5 deg steps), RX column
  fixed >= 2 m away, TDM on. Record the A and B levels (HPBW, sidelobes) and the
  **A-B phase difference vs angle** - K10's calibration curve.
- Measure outdoors, or with walls and floor far away, antennas >= 1.5 m high. A
  floor bounce ruins a pattern.

Done when both boards are characterized (S11, coupling, isolation vs spacing, gain
+-1 dB, azimuth pattern, A-B phase curve) and the numbers are written down.

### 9.6 TX power with patches

```
SDR_TX_GAIN_DB ~ 14 - G_tx + L_txcable + L_switch - 6.5   # dB; 6.5 dBm = typ port at 0 dB
```

`L_switch` is the IL measured in 8.2. Example: G_tx 13 dBi, 1.4 dB of cable, 1.3 dB
of switch gives -2.8; use -4 for margin. Put the same value into
`SDR_TX_GAIN_MAX_DB` for field sessions, so a typo cannot exceed the limit.

---

## 10. K10 - angle on the host; K11 - tracker in (x, y)

### 10.1 K10 - angle (TDM)

Read first: 13.4.

- `process_cpi` with `TDM_EN`: `A = full[0::2]`, `B = full[1::2]`, both up-chirps
  (no conj, no Doppler re-alignment). `T_rep = 2T` in `build_cpi_context` and
  `MAX_VELOCITY`, as triangle mode already does. Keep the **complex** RD matrices:
  angle needs the phase. Detect on `|X_A|^2 + |X_B|^2`.
- Per detection: `dphi = angle(X_B * conj(X_A))`. Remove the motion term:
  `psi = dphi - 2*pi*f_d*T - phi_cal`, where `f_d` is the detection's slow-time
  frequency read off the Doppler FFT's own frequency axis (sample interval `2T`,
  sub-bin refined) - not derived from v, which sidesteps the velocity sign
  convention. Then `theta = asin(psi * lambda / (2*pi*d))`, or a lookup on the K9
  turntable curve, which also absorbs mutual coupling.
- The residual from a half-bin error in `f_d` is <= 0.7 deg at 128 chirps per
  antenna, less after sub-bin refinement.
- **Sign**: `IF = TX * conj(RX)` conjugates the echo phase, so the sign of psi vs
  theta runs opposite to the textbook. Fix it by experiment (walk to one side) and
  write it into `CLAUDE.md` next to the Doppler sign.
- **Accuracy** (d = lambda/2): `sigma_dphi ~ 1/sqrt(SNR)` and
  `sigma_theta ~ sigma_dphi / (pi cos theta)`. That is 1.8 deg at 20 dB SNR, 0.6 deg
  at 30 dB. 1.8 deg is ~9.5 m of cross-range at 300 m.
- **Limits**: two virtual elements measure one angle per range-Doppler cell. Two
  targets in one cell give a blended angle, a ghost between them; separation comes
  from range and Doppler only. Accuracy collapses towards +-90 deg (the cos theta
  term).
- `TargetSim.apply_if` with `TDM_EN`: every row is an up-chirp (no sign flip on odd
  rows); odd rows get `exp(j pi sin(theta))` on top of the continuous Doppler, with
  the sign matched to the hardware once found. `FakeTarget` gains `theta0`. This
  tests the whole chain offline and in loopback before any antenna exists.
- Targets gain `theta`, `x = r sin(theta)`, `y = r cos(theta)`. GUI: a bird's-eye
  x-y scatter next to the RD maps.

Done when fake targets at +-30 deg come back within 1 deg at 0 and +-20 m/s (the
Doppler correction at work), and on the field a person walking a known line traces
it.

### 10.2 K11 - tracker in (x, y), field day 2

- State `[x, y, vx, vy]`, constant velocity. Measurement `(r, theta, v_r)` is
  nonlinear, so use an EKF with
  `h(x) = [sqrt(x^2 + y^2), atan2(x, y), (x vx + y vy) / r]`. `sigma_theta` comes
  from the detection's SNR, or a fixed ~2 deg to start.
- Association and management are unchanged. The gate becomes 11.34 (3 dof, 99 %).
- Field day 2, one change at a time: the RX column in place of the grid (COTS sector
  still TX), then the TX board through the switch with TDM on. Set the TX gain per
  9.6. Then a person walking a known path, a car along a road. Record everything.

Done when the tracks follow the known paths on the x-y plot.

---

## 11. Decision gate - fabric FFT/CFAR

Not planned in Phase 6. Revisit when one of these holds:

- After K1's fixes, host fps with TDM + tracking falls below what the tracker needs
  (~10-15 CPIs/s).
- A standalone sensor (detections-only link) or microsecond latency becomes a goal.

Design notes for when it comes:

- `N_IF` = 707 is not a power of two. The Xilinx FFT wants 512 or 1024: zero-pad
  to 1024, or pick `SWEEP_LEN` = 8 x 512 = 4096 (72 us) or 8 x 1024 = 8192 (145 us)
  so a leg is a power of two.
- Budget: 123/220 DSPs used (TDM adds none); a range + Doppler FFT is ~10-35 DSPs,
  and the corner turn is BRAM-bound (archived Phase 5 guide, Section 4.2).

---

## 12. Troubleshooting

- **Patch resonance >100 MHz off** -> eps_r. Re-extract it from the single patch
  (9.3) instead of trimming blindly.
- **Pattern full of ripple** -> multipath, usually a floor or wall bounce. Go
  outdoors and higher.
- **Isolation reads the same whatever the switch** -> you are at the VNA's floor
  (K4). Quote it as ">= floor".
- **Even and odd rows at the same level in 8.4** -> the switch is not switching:
  EN not low, no VDD, the wrong J30 pin, or `tdm_en` not committed.
- **The loud row changes between captures** -> the DMA sync still fires per chirp,
  not per pair (8.3).
- **Angle drifts with target speed** -> the Doppler term is missing or has the
  wrong sign (10.1).
- **Angles mirrored** -> the conj convention or A and B swapped (10.1). Fix by
  experiment, then write it down.
- **Track IDs swap on crossings** -> gate too wide or `sa` too large. v normally
  separates crossing targets.
- **RX saturates with the patches close together** -> isolation: more spacing, a
  metal fence between the boards, or lower RX gain. Check against the 9.5 S21
  numbers.

## 13. Reading - before each lane

Phase 6 is mostly new ground: RF measurement, antennas, estimation and tracking,
array signal processing. For each lane: the concepts worth owning before building,
what to read, and a small exercise that makes them stick. Read the lane's list
before its first step; the exercises are an hour or two each, in numpy, openEMS or
on paper.

### 13.1 RF measurement and soldering (before K4)

Concepts:
- Transmission lines: characteristic impedance, reflection, standing waves,
  electrical length, velocity factor, loss per metre and why it rises with
  frequency.
- S-parameters: S11 and S21, return loss, VSWR, insertion loss, isolation; enough
  Smith chart to read a match.
- VNA calibration: error terms, SOLT, reference planes, what calibration removes
  and what it cannot (a bad connector after the reference plane).
- Reading an RF component datasheet: IL, isolation, return loss, P1dB, switching
  time, control logic levels.
- Soldering: wetting, heat transfer, why flux matters, what a good joint looks
  like; ESD.

Read:
- Pozar, *Microwave Engineering* (Wiley): transmission lines, microwave network
  analysis (S-parameters), the Smith chart.
- Hiebel, *Fundamentals of Vector Network Analysis* (Rohde & Schwarz): calibration
  and error terms, for when a reading looks wrong.
- Your VNA's manual, including its calibration procedure.
- Adafruit, "Adafruit Guide to Excellent Soldering" (learn.adafruit.com).

Exercises:
- Predict S21 for 2 m of HF240 and for one VAT-15A+ at 5.8 GHz before K4 measures
  them.
- On a paper Smith chart: an open-ended 50 ohm line, S11 vs electrical length. Then
  measure it.
- Read the HMC8038 datasheet and write one line per spec: what it means, and what it
  does to the radar (IL -> TX power, isolation -> angle error, switching time ->
  boundary transient).

### 13.2 Antennas (before K8)

Concepts:
- Microstrip: effective permittivity, guided wavelength (why a series-fed pitch is
  ~lambda_g), 50 ohm line width.
- The patch itself: the cavity and transmission-line models, fringing and the
  resonant length, inset-feed impedance, bandwidth vs substrate thickness and
  eps_r, surface waves.
- Antenna parameters: directivity vs gain vs efficiency, HPBW, sidelobes,
  polarization, the far field (`2 D^2 / lambda`), Friis.
- Arrays: array factor, grating lobes, series vs corporate feeds, amplitude taper
  (Dolph-Chebyshev, Taylor) vs sidelobes, mutual coupling.
- EM simulation (FDTD): mesh resolution (~lambda/20 in the dielectric, finer at
  edges), ports, absorbing boundaries (PML), checking convergence.

Read:
- Balanis, *Antenna Theory: Analysis and Design* (Wiley): the chapters on
  fundamental antenna parameters, on arrays, and on microstrip antennas. The patch
  equations behind 9.3 come from the last one. The standard reference.
- openEMS tutorials (docs.openems.de), "Simple Patch Antenna": run it unchanged
  first, then retarget it to 5.8 GHz on FR4.
- Garg, Bhartia, Bahl, Ittipiboon, *Microstrip Antenna Design Handbook* (Artech
  House): practical design data, including series-fed arrays.
- Search terms for the column: "series-fed microstrip patch array", "comb-line
  array", "automotive radar antenna" (24/77 GHz radars use the same topology).

Exercises:
- Compute W, L and the 50 ohm line width by hand from the Balanis and Pozar
  formulas, and compare with 9.3.
- Simulate your 5.8 GHz patch and sweep eps_r over 4.2-4.6. Watching the
  resonance move is the argument for K8's calibration spin.
- Plot array factors in numpy: 8 elements at 0.55 lambda (the column in
  elevation), two elements at lambda/2 and at 2.8 lambda (9.2's ambiguity table).

### 13.3 Tracking (before K3)

Concepts:
- The Kalman filter: predict and update, covariance, innovation, Kalman gain, and
  why Q and R are the only real tuning knobs.
- Kinematic models: constant velocity with discretized white-noise acceleration
  (the Q in 4.2), constant acceleration, and how `sa` trades lag vs noise.
- Filter consistency: NEES and NIS tests. Tune `sa`, `sr`, `sv` with these, not by
  eye.
- Data association: gating (Mahalanobis distance, chi-square), global nearest
  neighbour as an assignment problem (Hungarian algorithm), and the heavier
  alternatives (JPDA, MHT) - and why GNN is enough at these target densities.
- Track management: M-of-N confirmation, deletion, and the sequential track-score
  (SPRT) alternative.
- Nonlinear measurements for K11: the EKF and its Jacobians, the
  converted-measurement alternative, the UKF.
- The radar-specific advantage: a measured radial velocity makes initiation
  single-shot and association much easier than with position-only sensors.

Read:
- Labbe, *Kalman and Bayesian Filters in Python* (free, GitHub
  `rlabbe/Kalman-and-Bayesian-Filters-in-Python`): start here. g-h filter, KF,
  multivariate KF, EKF, all runnable.
- Bar-Shalom, Li, Kirubarajan, *Estimation with Applications to Tracking and
  Navigation* (Wiley): the reference for kinematic models, NEES/NIS consistency
  and the EKF. Read after Labbe.
- Blackman and Popoli, *Design and Analysis of Modern Tracking Systems* (Artech
  House): gating, assignment, initiation, confirmation and deletion as fielded
  radars do them.
- `scipy.optimize.linear_sum_assignment` documentation.

Exercises:
- A 1D constant-velocity KF on a synthetic target: plot the NIS over time and
  check its mean is ~2 (the measurement dimension). Mis-tune `sa` by 10x each way
  and watch the NIS and the lag.
- Two crossing targets with and without the v measurement: see how much the
  measured velocity buys in association.

### 13.4 Angle estimation and TDM-MIMO (before K7 and K10)

Concepts:
- Phase interferometry and the uniform linear array steering vector; lambda/2
  spacing and ambiguity.
- MIMO virtual arrays: M TX and N RX act like M x N receivers at the sums of the
  positions; TDM separates the TX in time.
- Motion in TDM: the Doppler phase between TX slots, and why it must come out before
  the angle.
- The Cramer-Rao bound for angle vs SNR (where 10.1's 1.8 deg at 20 dB comes from).
- Array calibration: channel phase and gain offsets, mutual coupling.
- Beamforming and the angle FFT, for more than two elements.

Read:
- Texas Instruments, "Introduction to mmWave Sensing: FMCW Radars" (training
  series by Sandeep Rao): range, velocity and angle estimation on the same
  processing chain as this radar.
- Texas Instruments application report "MIMO Radar" (SWRA554): virtual arrays and
  TDM-MIMO.
- Richards, *Fundamentals of Radar Signal Processing* (McGraw-Hill): Doppler
  processing, CFAR and detection theory for what already exists, and the
  beamforming chapter for angle.

Exercise:
- Simulate 2T1R TDM in numpy: a target at theta and v, estimate theta with and
  without the Doppler correction, and plot the angle error vs v. Then check
  sigma_theta vs SNR against 10.1's formula.

## Appendix A - Phase 6 kit

Owned: pads, terminations, HF240 cables, VBFZ filters, jumpers, COTS antennas,
tripods (`hardware/materials.md`).

To buy, in the order the steps need it:

- K4: VNA to >= 6 GHz (LiteVNA-64 class) with SMA calibration standards.
- K5: temperature-controlled soldering iron with a fine conical and a small chisel
  tip, 0.5-0.8 mm leaded solder, flux, desoldering wick, a tip cleaner, ventilation;
  a straight 10-pin 2.54 mm header; female-female Dupont leads; a practice board.
- K6: a trihedral corner reflector, ~30 cm edges (stiff cardboard and kitchen foil
  will do for the range check).
- K7: the HMC8038 switch module (8.1), two of them.
- K8/K9: PCBs, FR4 1.6 mm, 2 layers (RO4003C quote as fallback); edge-mount SMAs
  rated well past 6 GHz.
- K9: a turntable (lazy susan + printed protractor) and a tripod mount for the
  boards.

## Appendix B - 2R2T fallback (K0 results, 2026-09-28)

K0 passed on the board; the procedure and details are in `CLAUDE.md` ("2R2T mode").
In short:

- **Switch**: one dtb property on the SD card,
  `fdtput -t s devicetree.dtb /amba/spi@e0006000/ad9361-phy@0 compatible adi,ad9361`.
  The card keeps `devicetree_1r1t.dtb` / `devicetree_2r2t.dtb`.
- **Cap**: FS <= 30.72 MSPS; 56.6 returns EINVAL ("Failed CMOS MODE DATA_CLK >
  61.44MSPS", `ad9361_validate_trx_clock_chain`). Sources: AD9361 datasheet (CMOS
  DATA_CLK 61.44 MHz max), UG-570 (the digital interface chapter), ADI's Pluto
  "hacking" page ("maximum sample rate is reduced from 61.44 MSPS to 30.72 MSPS"),
  the E200 schematic (AD9361 bus on bank 34 at 1.8 V).
- **Proven**: the fabric is transparent to 2R2T (`decim_ramp_check.py` bit-exact,
  `dechirp_verify.py --ab` PASS at 30.72 MSPS / 25 MHz); loopback dechirp delay 20
  at 30.72 MSPS; RX2 data path alive. Open: RX2 on a real signal.
- **Connectors**: RX2 and TX2 are IPEX (U.FL); short IPEX-to-SMA pigtails.
- **What 2R would still need** (the old K7): a second `mixer_dechirp` on channels
  2/3 with the same DECHIRP_DELAY, a second decimator pair, identical latency (a
  channel skew is a phase error that grows with range, which boresight calibration
  cannot remove), +~52 DSPs; verified by identical-stimulus VUnit and a splitter
  sweep.
- **Later option**: in 2R2T, TX2 is live too, and the fabric could put the NCO on
  TX1 or TX2 per chirp: 2T2R TDM, four virtual elements, no external switch - the
  step to take if angle *resolution* is ever wanted. Still 30.72 MSPS.
- **Preview of 6 m bins without any of this**: replay a 50 MHz recording using only
  the first half of each chirp's samples (25 MHz of sweep, 6 m bins, 3 dB less SNR).
