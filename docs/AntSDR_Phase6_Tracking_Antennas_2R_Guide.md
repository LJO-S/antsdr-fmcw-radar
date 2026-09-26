# AntSDR E200 - Phase 6: tracking, patch antennas, 2R1T angle (Part K)

Follows Phase 5 (archived). Assumes: Parts A-G and I done. The fabric chirp NCO,
dechirp and /8 decimation are proven in digital loopback (MAGIC FMC4, up to ~30
fps); Part G passed in software mode with the COTS antennas (sector + dish, ~1.5 m
apart, all defaults). The fabric path has not yet seen real RF.

Same contract as before: this guide specifies, you write the code. Register map,
sequencing rules and conventions live in `CLAUDE.md`; this guide only states what
is new.

## 1. What and why

Three candidates were on the table: custom antennas, software tracking, fabric
FFT/CFAR. The order, and why:

| Work | Payoff | Gated by | Order |
|---|---|---|---|
| Patch antennas | Compact rig, and the only route to angle (2R1T) | PCB and VNA lead times, weather for field tests | Start first, runs in the background |
| Software tracking | Stable IDs, smoothed v, fewer one-CPI false alarms. First user of I5's frame rate | Nothing | Fill the antenna waits |
| Fabric FFT/CFAR | Latency, detections-only link | A real need, which does not exist yet | Last, behind a decision gate (Section 10) |

Why the fabric FFT goes last: the ~30 fps falls as detections rise, so the host
bottleneck sits after the FFT (CFAR/NMS/sub-bin/pairing or the GUI scatter). A
128 x 707 FFT is a few ms in numpy. Moving the FFT into fabric alone would not raise
the frame rate.

Two lanes run in parallel:

```
software lane:  K1 profile -> K2 recorder -> K3 tracker (r, v)
antenna lane:   K0 2R2T check -> K4 single patch -> K5 columns + bench
                              \-> K6 field day 1 (whenever the weather allows; needs K2)
then:           K7 2R in fabric -> K8 angle on host -> K9 tracker (x, y) + field day 2
gate:           Section 10, fabric FFT/CFAR
```

Each lane opens with reading: Section 12 lists the concepts, sources and a short
exercise for antennas (before K4), tracking (before K3) and angle (before K8).

### The link-budget consequence of patches

Under the 25 mW SRD EIRP cap, **TX antenna gain is free**: a lower-gain TX antenna
just takes more TX port power, and the E200 has headroom (~+6.5 dBm vs the -5 dBm
used with the 19 dBi sector). **RX gain is the cost**: range goes as `G_rx^(1/4)`.

| RX antenna | vs the 30 dBi dish | Range factor |
|---|---|---|
| 1x8 column, 15 dBi (Rogers) | -15 dB | 0.42 |
| 1x8 column, 13 dBi (FR4) | -17 dB | 0.38 |
| two 13 dBi columns, coherent sum | -14 dB | 0.45 |

So roughly half the range: a person at a few hundred metres instead of ~1 km, cars
further, still inside the 800 m decimation envelope. The beams get wide (~70-80 deg
in azimuth vs ~5 deg for the dish), so more ground clutter enters and MTI will
matter more than it did in Part G.

---

## 2. K0 - 2R2T boot check (board, 30 min)

Do this before laying out a two-RX board: it is the gate for the RX pair.

The E200 runs the 1R1T (AD9364) personality by default. The firmware README
("Support 2r2t mode", SD mode) switches it at boot with four `uEnv.txt` edits on
the SD card:

1. `adi_loadvals=fdt addr ${devicetree_load_address}...` (not `${fit_load_address}`).
2. `mode=2r2t`.
3. `sdboot=...` gains `&& run adi_loadvals;` before `bootm`, as in the README.
4. Append `attr_name=compatible`, `attr_val=ad9361`, `compatible=ad9361`.

The boot script keeps `attr_val=ad9361` only when the DT model is
`Analog Devices ANTSDR Rev.C (Z7020/AD9363)`, which is exactly what `zynq-e200.dtsi`
sets. Any other value gets rewritten to `ad9363a`, whose driver limits would reject
5.8 GHz. So check the LO still takes 5.8e9 after the switch.

Checks, in order:

- `iio_info -u ip:192.168.5.10`: `ad9361-phy` model ad9361 (not ad9364),
  `cf-ad9361-lpc` exposes `voltage0..3`.
- `python -m online.app` in software mode: `start()` accepts 5.8 GHz and FS 56.6
  MSPS (the datasheet allows 61.44 MSPS in LVDS 2R2T; confirm the board agrees).
- `decim_ramp_check.py` and `dechirp_verify.py --ab` still pass. The interface
  cadence changes in 2R2T and the core runs on valid, so this should be
  transparent - prove it.
- RX2 alive: a throwaway script enabling `voltage2/3`, cable + >= 30 dB pad from TX1
  into RX2, the chirp visible in the spectrogram.

Done when all four hold. If 2R2T will not come up, the antenna lane still works
with one RX column and the angle steps drop out.

---

## 3. K1 - profile the host (no board)

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
knowingly accepted. Write the ms-per-stage table into `TODO.md`: K3 and K8 add
per-CPI work on top of it.

---

## 4. K2 - recorder and replay (no board)

Part G produced video only, so nothing from it can be replayed. From now on every
field day records IQ. The recordings drive the tracker (K3) and the angle work (K8)
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

## 5. K3 - tracker in range and velocity (no board)

Read first: 12.2.

### 5.1 Conventions - check these before writing F

- **Sign**: `TargetSim` moves `r = r0 + v0*t`, and `test_target_sim.py` asserts
  that `process_cpi` reports `v0` with that sign. So **v > 0 is receding**, and
  `dr/dt = +v`. Confirm on the first real recording: an approaching car must read
  v < 0.
- **Time**: `dt` comes from the CPI timestamps (monotonic, at capture return),
  never from a nominal frame period. The frame rate varies with detection count.

### 5.2 Model

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

### 5.3 Association and track management

- Gate: Mahalanobis `d^2 = y' S^-1 y < 9.21` (chi-square, 2 dof, 99 %).
- Global nearest neighbour: `scipy.optimize.linear_sum_assignment` on `d^2`, with
  out-of-gate pairs set to a large cost. Unassigned detections start tentative
  tracks.
- Confirm after M of N CPIs (default 3 of 4). Delete by **time**, not by CPI count:
  a confirmed track after 0.5 s without an update, a tentative one after 2 misses.
- `TRACK_MIN_SPEED` (default 0 = off): minimum |v| to start a track, for scenes
  full of parked cars when MTI is off.
- Velocity ambiguity is not an issue: `MAX_VELOCITY` is 129 m/s (64 m/s in
  triangle).
- Triangle mode: accept every `kind`. Weighting `R` by kind is a later refinement.

### 5.4 Placement

- `common/tracking.py`: pure numpy/scipy, no Qt. `Tracker(cfg).update(detections,
  t) -> list[Track]`. `Track` = id, state, covariance, status, hits, misses, and a
  short history of `(t, r)`.
- **Keep the motion/measurement model behind one small interface** (predict,
  predicted measurement, Jacobian, R). K9 swaps in a 2D model with a nonlinear
  measurement; association and track management must not change.
- The worker owns the tracker (state across CPIs), calls it right after
  `process_rx_data`, and rebuilds it on RE-CONFIGURE. Tracks go to the GUI on a new
  `tracks` signal, so `results` stays unchanged.
- GUI: tracks on the existing detections scatter, as labelled markers with a short
  tail.
- Config: `TRACK_EN`, `TRACK_SIGMA_A`, `TRACK_SIGMA_R`, `TRACK_SIGMA_V`,
  `TRACK_CONFIRM_M/N`, `TRACK_DELETE_S`, `TRACK_MIN_SPEED`, group `tracking`. The
  GUI rows come for free.

### 5.5 Tests (`common/test_tracking.py`, `__main__` asserts)

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

## 6. K4, K5 - patch antennas

Read first: 12.1.

### 6.1 Targets

- **Match**: S11 < -10 dB over 5.75-5.85 GHz (the chirp spans 5.775-5.825).
- **Gain**: >= 13 dBi per column on FR4.
- **Beam**: azimuth HPBW >= 60 deg, elevation ~12 deg.
- **Polarization**: linear, the same on TX and RX. When mixing with the COTS
  antennas, match their polarization.

### 6.2 Why columns, not square arrays

Angle from two RX channels comes from their phase difference, and it is only
unambiguous for `|sin(theta)| < lambda / (2 d)`, with `d` the distance between the
two phase centres.

| Centre spacing d | Unambiguous field of view |
|---|---|
| lambda/2 = 25.9 mm | +-90 deg |
| 0.6 lambda = 31 mm | +-56 deg |
| two 4x4 arrays side by side (~2.8 lambda) | +-10 deg, about the beam itself |

A series-fed **1x8 column** is ~16 mm wide, so two of them fit at lambda/2. They
give a narrow elevation beam, a wide azimuth beam, and an angle that is unambiguous
across it. This is the standard automotive FMCW layout.

### 6.3 K4 - single patch first

- Substrate: FR4, 1.6 mm, 2 layers. Nominal eps_r 4.4, but 4.2-4.6 in practice
  at 5.8 GHz, tan d ~0.02. That spread moves resonance by 100-250 MHz, more than
  the patch bandwidth, which is why a calibration spin comes first.
- Starting dimensions (transmission-line model, eps_r 4.4, h 1.6 mm): W ~ 15.7 mm,
  L ~ 11.8 mm, inset feed from a 50 ohm line (~3.0 mm wide). Final numbers come
  from openEMS.
- On the same panel: a plain 50 ohm through line (loss and eps_eff) next to the
  patch.
- Edge-mount SMAs rated well past 6 GHz.
- Measure S11. Back out eps_r from the resonance shift:
  `eps_r_real ~ eps_r_sim * (f_sim / f_meas)^2`. Re-run the simulation with it
  before K5.
- Fallback: Rogers RO4003C, 0.508 mm (eps_r 3.38 +-0.05, tan d 0.0027), if FR4
  batch spread or loss is too much.

Done when the corrected simulation predicts the measured resonance within +-50 MHz.

### 6.4 K5 - columns

- Series-fed 1x8. Element pitch ~ one guided wavelength of the connecting line,
  ~28 mm on FR4 (0.55 lambda0 in free space, so no grating lobes). The column is
  ~23 cm long.
- Uniform amplitude first. A Dolph/Taylor taper through the patch widths comes
  second: elevation sidelobes are where ground clutter gets in.
- **TX board**: one column.
- **RX board**: two columns, centre spacing lambda/2 = 25.9 mm (0.6 lambda if the
  coupling is worse than -15 dB). Identical feeds with equal line lengths to the
  SMAs, so the channel phase offset is nearly constant; calibration takes the rest.
- Expected: ~13 dBi on FR4, ~15 dBi on Rogers, azimuth HPBW ~70-80 deg, elevation
  ~11-12 deg.

### 6.5 K5 - bench measurements

- **VNA to >= 6 GHz** (a LiteVNA-64 class unit; a NanoVNA-H4 stops at 1.5 GHz).
  SOL calibration at the SMA.
- **S-parameters**:
  - S11 of every column.
  - S21 between the two RX columns (coupling, target <= -15 dB).
  - S21 between the TX and RX boards vs spacing (isolation - this replaces Part G's
    skipped isolation item). Reference: the leakage the E200 sees with the COTS
    antennas at 1.5 m, measured once with the same RX gain.
- **Gain, two-antenna method with the E200**: two identical columns face to face at
  R >= 2 m (far field `2 D^2 / lambda` ~ 1.9 m for a 23 cm column).
  - Reference: TX cable -> known pad -> RX cable. Read the peak level in the range
    profile (`dechirp_verify.range_profile_db`: no close-in mask).
  - Then swap the pad for the two antennas, same RX gain:

    ```
    G_dBi = 0.5 * (level_ant - level_ref - pad_dB + FSPL(R))
    FSPL(R) = 20*log10(4*pi*R / lambda)      # 53.7 dB at 2 m, 57.3 dB at 3 m
    ```

  - The pad keeps RX below its +2.5 dBm absolute maximum; keep the TX low.
- **Pattern**: rotate the RX board on a marked turntable (5 deg steps) and record
  the link level: HPBW and sidelobes. With K0 done, record the **channel phase
  difference vs angle** at the same time - that is K8's angle calibration curve.
- Measure outdoors, or with walls and floor far away, antennas >= 1.5 m high. A
  floor bounce ruins a pattern.

Done when both boards are characterized (S11, coupling, isolation vs spacing, gain
+-1 dB, azimuth pattern) and the numbers are written down.

### 6.6 TX power with patches

```
SDR_TX_GAIN_DB ~ 14 - G_tx + L_txcable - 6.5          # dB; 6.5 dBm = typ port at 0 dB
```

Example: G_tx 13 dBi, 1.4 dB of cable gives -4.1; use -5 for margin. Put the same
value into `SDR_TX_GAIN_MAX_DB` for field sessions, so a typo cannot exceed the
limit.

---

## 7. K6 - field day 1: fabric on real RF (board, weather)

Needs K2. The COTS antennas come first, because they are known-good RF - never
debug new RF and new HDL at once.

1. **COTS, software mode**, recording on: a static scene, then movers. Put a corner
   reflector at a tape- or laser-measured range. A trihedral with 30 cm edges is
   ~12.7 m^2 (`4 pi a^4 / (3 lambda^2)`). It is also the end-to-end range-axis check
   Part F skipped.
2. **Delay**: `dechirp_verify.py --no-loopback --sweep` (`SDR_LOOPBACK_EN = False`).
   Expect 61 +-1: 1.5 m of air is ~5 ns, ~0.3 samples.
3. **A/B**: `dechirp_verify.py --no-loopback --decimate 8`, software vs fabric /8.
   Compare only the static content (leakage, reflector, clutter); movers differ
   between the two phases.
4. **Fabric /8 in the app**, recording on: cars, people on foot, bikes, at several
   ranges, plus a quiet stretch.
5. **Patches, if ready**: RX column in place of the dish (keep the COTS TX), then
   the TX column too. Set the TX gain per 6.6. Record each configuration.

Done when fabric /8 matches software mode on real RF and the delay is recorded.
Then flip the `FABRIC_DECHIRP_EN` default.

---

## 8. K7 - second RX channel in fabric (HDL + VUnit)

### 8.1 What exists

The block design already routes `axi_ad9361/adc_data_i1/q1` (and enables) into
`fmcw_core/i_adc_data_2/3`, and `o_adc_data_2/3` into `cpack` channels 2/3. **No
block-design change is needed.** Inside the core, channels 2/3 are a one-register
passthrough: not muxed, not dechirped, not decimated. With DECIM_SEL=1,
`o_adc_valid` is the decimated valid while channels 2/3 still carry full-rate
samples, so cpack would pack garbage.

### 8.2 Changes (MAGIC -> FMC5 = `0x464D4335`: channels 2/3 change meaning)

- **Second `mixer_dechirp`** on `i_adc_data_2/3`, with its own delay line at the
  same DECHIRP_DELAY. Duplicating it makes the latency identical by construction;
  the extra ~128 x 34-bit delay line is cheap.
- **Same output mux** for channels 2/3 as for 0/1 (IF / passthrough; ramp on both,
  so the ramp test covers both chains).
- **Second decimator pair** (I/Q), restarted by the second mixer's own tag lane.
- **Equal latency is a hard requirement.** A skew between the channels is a phase
  error that grows with range, `2 pi f_b k / fs`, and a boresight calibration
  cannot remove it. At the band edge (2.8 MHz) one raw sample is 18 deg; one IF
  sample after /8 is 143 deg.
- **DSPs**: +~52 as built (~175/220), +~28 with the halfband fold fix (I5b lever,
  ~127). Both fit; the fold fix leaves room for a later FFT.

### 8.3 Verification

- `tb_fmcw_core`: identical stimulus on both channels -> bit-identical outputs in
  the same cycle (the equal-latency test). Distinct stimulus -> each channel
  bit-exact vs `decimate_golden(mixer_golden(...))`.
- Hardware, in 2R2T:
  - `decim_ramp_check.py` on both channels.
  - **Bench latency check**: TX1 -> >= 30 dB pad -> a 6 GHz two-way splitter ->
    RX1 and RX2 over equal cables. Sweep DECHIRP_DELAY from 60 down to 0: the cable
    path walks from range bin ~0 to ~54 (up to ~160 m equivalent at 50 MHz /
    100 us). The channel phase difference at the peak must stay constant (to within
    noise) across the sweep. A linear drift means unequal latency.

Digital loopback cannot test channel 2: it loops TX2 into RX2, and TX2 carries
nothing.

Done when VUnit is green, the ramp is bit-exact on both channels, and the splitter
sweep shows a flat phase difference.

---

## 9. K8 - angle on the host; K9 - tracker in (x, y)

### 9.1 K8 - angle

Read first: 12.3.

- `sdr.py`: `SDR_RX2_EN` enables `voltage2/3`. A sample becomes 8 bytes;
  deinterleave `raw[0::4] + 1j*raw[1::4]` and `raw[2::4] + 1j*raw[3::4]`. The
  buffer doubles. The recorder stores all four lanes.
- `process_cpi` per channel. Angle needs the **complex** RD cells, not the dB maps
  - keep the complex matrices. Detect on the non-coherent sum
  (`|X1|^2 + |X2|^2`): +3 dB, and no dependence on the unknown phase.
- Per detection cell: `dphi = angle(X2 * conj(X1)) - phi_cal`, then
  `theta = asin(dphi * lambda / (2 pi d))`, or a lookup on the K5 turntable curve,
  which also absorbs mutual coupling.
- **Sign**: `IF = TX * conj(RX)` conjugates the RX phase, so the sign of dphi vs
  theta is the reverse of the textbook. Fix it by experiment (walk to one side) and
  write it into `CLAUDE.md` next to the Doppler sign.
- **Accuracy** (d = lambda/2): `sigma_dphi ~ 1/sqrt(SNR)` and
  `sigma_theta ~ sigma_dphi / (pi cos theta)`. That is 1.8 deg at 20 dB SNR, 0.6 deg
  at 30 dB. 1.8 deg is ~9.5 m of cross-range at 300 m.
- **Limits**: two elements measure one angle per range-Doppler cell. Two targets in
  the same cell give a blended angle; separation comes from range and Doppler, not
  angle. Accuracy collapses towards +-90 deg (the cos theta term).
- `TargetSim`: `FakeTarget` gains `theta0`. `apply_if` writes channel 2 as
  channel 1 times `exp(j pi sin(theta))`, so the angle path can be tested offline
  and in loopback.
- Targets gain `theta`, `x = r sin(theta)`, `y = r cos(theta)`. GUI: a bird's-eye
  x-y scatter next to the RD maps.

Done when fake targets at +-30 deg come back within 1 deg, and on the field, a
person walking a known line traces it.

### 9.2 K9 - tracker in (x, y), field day 2

- State `[x, y, vx, vy]`, constant velocity. Measurement `(r, theta, v_r)` is
  nonlinear, so use an EKF with
  `h(x) = [sqrt(x^2 + y^2), atan2(x, y), (x vx + y vy) / r]`. `sigma_theta` comes
  from the detection's SNR, or a fixed ~2 deg to start.
- Association and management are unchanged. The gate becomes 11.34 (3 dof, 99 %).
- Field day 2: patch boards, fabric /8, 2R. A person walking a known path, a car
  along a road. Record everything.

Done when the tracks follow the known paths on the x-y plot.

---

## 10. Decision gate - fabric FFT/CFAR

Not planned in Phase 6. Revisit when one of these holds:

- After K1's fixes, host fps with 2R + tracking falls below what the tracker needs
  (~10-15 CPIs/s).
- A standalone sensor (detections-only link) or microsecond latency becomes a goal.

Design notes for when it comes:

- `N_IF` = 707 is not a power of two. The Xilinx FFT wants 512 or 1024: zero-pad
  to 1024, or pick `SWEEP_LEN` = 8 x 512 = 4096 (72 us) or 8 x 1024 = 8192 (145 us)
  so a leg is a power of two.
- Budget: a range + Doppler FFT is ~10-35 DSPs, and the corner turn is BRAM-bound
  (archived Phase 5 guide, Section 4.2).

---

## 11. Troubleshooting

- **Patch resonance >100 MHz off** -> eps_r. Re-extract it from the single patch
  (6.3) instead of trimming blindly.
- **Pattern full of ripple** -> multipath, usually a floor or wall bounce. Go
  outdoors and higher.
- **RX2 dead after the 2r2t switch** -> an incomplete `uEnv.txt` edit. `iio_info`
  must show ad9361 and `voltage0..3`.
- **5.8 GHz rejected after the switch** -> `attr_val` got rewritten to `ad9363a`.
- **Channel phase difference drifts with range** -> unequal channel latency (8.2).
  Run the splitter sweep.
- **Angles mirrored** -> the conj convention or swapped channels (9.1). Fix by
  experiment, then write it down.
- **Track IDs swap on crossings** -> gate too wide or `sa` too large. v normally
  separates crossing targets.
- **RX saturates with the patches close together** -> isolation: more spacing, a
  metal fence between the boards, or lower RX gain. Check against the 6.5 S21
  numbers.

## 12. Reading - before each lane

Phase 6 is mostly new ground: EM and antennas, estimation and tracking, array
signal processing. For each lane: the concepts worth owning before building, what
to read, and a small exercise that makes them stick. Read the lane's list before
its first step; the exercises are an hour or two each, in numpy or openEMS.

### 12.1 Antennas (before K4)

Concepts:
- Transmission lines: characteristic impedance, microstrip effective permittivity,
  guided wavelength (why a series-fed pitch is ~lambda_g).
- S-parameters, return loss and S11, VSWR, enough Smith chart to read a match;
  calibration and reference planes (what SOL at the SMA actually removes).
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
  equations behind 6.3 come from the last one. The standard reference.
- Pozar, *Microwave Engineering* (Wiley): transmission lines, microstrip,
  microwave network analysis (S-parameters), impedance matching.
- openEMS tutorials (docs.openems.de), "Simple Patch Antenna": run it unchanged
  first, then retarget it to 5.8 GHz on FR4.
- Garg, Bhartia, Bahl, Ittipiboon, *Microstrip Antenna Design Handbook* (Artech
  House): practical design data, including series-fed arrays.
- Search terms for the column: "series-fed microstrip patch array", "comb-line
  array", "automotive radar antenna" (24/77 GHz radars use the same topology).
- Hiebel, *Fundamentals of Vector Network Analysis* (Rohde & Schwarz): calibration
  and error terms, for when a VNA reading looks wrong.

Exercises:
- Compute W, L and the 50 ohm line width by hand from the Balanis and Pozar
  formulas, and compare with 6.3.
- Simulate your 5.8 GHz patch and sweep eps_r over 4.2-4.6. Watching the
  resonance move is the argument for K4's calibration spin.
- Plot array factors in numpy: 8 elements at 0.55 lambda (the column in
  elevation), two elements at lambda/2 and at 2.8 lambda (6.2's ambiguity table).

### 12.2 Tracking (before K3)

Concepts:
- The Kalman filter: predict and update, covariance, innovation, Kalman gain, and
  why Q and R are the only real tuning knobs.
- Kinematic models: constant velocity with discretized white-noise acceleration
  (the Q in 5.2), constant acceleration, and how `sa` trades lag vs noise.
- Filter consistency: NEES and NIS tests. Tune `sa`, `sr`, `sv` with these, not by
  eye.
- Data association: gating (Mahalanobis distance, chi-square), global nearest
  neighbour as an assignment problem (Hungarian algorithm), and the heavier
  alternatives (JPDA, MHT) - and why GNN is enough at these target densities.
- Track management: M-of-N confirmation, deletion, and the sequential track-score
  (SPRT) alternative.
- Nonlinear measurements for K9: the EKF and its Jacobians, the
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

### 12.3 Angle estimation (before K8)

Concepts:
- Phase interferometry and the uniform linear array steering vector; lambda/2
  spacing and ambiguity.
- The angle FFT once there are more than two elements; beamforming.
- The Cramer-Rao bound for angle vs SNR (where 9.1's 1.8 deg at 20 dB comes from).
- Array calibration: channel phase and gain offsets, mutual coupling.
- MIMO virtual arrays: 2T2R with TDM gives four virtual elements - the natural
  next step after 2R1T.

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
- Simulate two noisy channels in numpy: estimate theta from the phase
  difference, and check sigma_theta vs SNR against 9.1's formula.

## Appendix - Phase 6 kit

- VNA to >= 6 GHz (LiteVNA-64 class), SMA calibration kit.
- PCBs: FR4 1.6 mm, 2 layers; RO4003C quote as fallback.
- Edge-mount SMA connectors rated well past 6 GHz; 6 GHz-rated cables and pads
  (see `hardware/materials.md`: many cheap parts stop at 3 GHz).
- A two-way splitter rated to 6 GHz (K7 bench latency check).
- A trihedral corner reflector, ~30 cm edges, aluminium sheet.
- A turntable (lazy susan + printed protractor) and a tripod mount for the boards.
