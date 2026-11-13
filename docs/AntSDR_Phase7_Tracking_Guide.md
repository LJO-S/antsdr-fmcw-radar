# AntSDR E200 - Phase 7: tracking (Part L)

Runs in parallel with Phase 6 (Part K). Split out of Phase 6 on 2026-11-05: it takes K3
(tracker in range and velocity) and the tracker half of K11 (x, y). Phase 6 keeps the field
days, which are RF work, and they record the sessions this phase validates on (Section 12).

Same contract as before: this guide specifies, you write the code. Conventions live in
`CLAUDE.md` and `.claude/rules/` (`dsp.md` for the detection pipeline, `app.md` for the
worker and GUI); this guide only states what is new. "G6" = the Phase 6 guide.

## 1. What and why

Today each CPI's detections go straight to the screen: a list that flickers at 25 CPI/s
with targets near the threshold, CFAR false alarms (~0.1 per CPI), the four chirp spurs
and, in triangle mode, up-only and down-only detections. A tracker turns them into tracks:
stable IDs, smoothed range and velocity (later x and y), and no one-CPI false alarms on
screen. It is also the project's first estimation work: Kalman filters, consistency
tests, data association.

| Step | Payoff | Gated by |
|---|---|---|
| L0 reading + exercises | Kalman/NIS intuition, the gtrack blueprint | Nothing |
| L1 SNR per detection | Measurement noise per detection | Nothing |
| L2 tracker in (r, v) | Tracks from detections, offline tests | L1 |
| L3 app, playback, replay | Tracks live and on recordings | L2 |
| L4 GUI | PPI-first Radar tab, track list | L3 |
| L5 tracker in (x, y) | EKF on (r, theta, v_r) | L2 (L4 to display) |
| L6 real targets | Validation on field recordings | K6 for (r, v), field day 2 for (x, y) |

```
L0 reading -> L1 SNR -> L2 tracker (r, v) -> L3 app/replay -> L4 GUI
                                 \
                                  \
                                  --> L5 tracker (x, y), fake targets with theta0
Phase 6 K6, field day 1 ----------------------------------> L6a validate (r, v)
Phase 6 field day 2 (patches + TDM on air, psi0 set) -----> L6b validate (x, y)
```

**Done** when the synthetic tests pass, loopback fake targets track live, and real targets
track on recordings: (r, v) on K6's, (x, y) on field day 2's. So the phase closes after
Phase 6's field days; everything before L6 can run now.

**Not in this phase**: drones; JPDA, MHT, PMBM/PHD; a particle filter; clustering in the
core; recording tracks (they are deterministic from detections and timestamps, so replay
regenerates them); tracking in the fabric.

## 2. The problem as this radar poses it

- **Measured** per detection: range r and radial velocity v; with `TDM_EN` also theta
  (1.8 deg at 20 dB SNR, 4 deg at 13 dB, worse off boresight; G6 10.1); SNR from L1.
- **Targets**: cars at 0-80 km/h (<= 22 m/s), people on foot, bikes. Accelerations: cars
  2-3 m/s^2, ~8 in hard braking; people and bikes 1-3. Every mode's `MAX_VELOCITY` is above
  22 m/s (lowest: 32 m/s in triangle + TDM), so nothing aliases short of ~115 km/h. A
  target first seen above it can't be tracked: its aliased v predicts the wrong range
  (gtrack resolves this by unrolling Doppler; out of scope).
- **Detections per target**: Part G showed mostly one dot per car, person or bike,
  sometimes two for walkers. NMS (`dsp.NMS_SIZE` 3, a 7x7 window, `dsp.py` `nms`) already acts
  as a rough cluster: +-3 range bins (+-9 m) and +-3 velocity bins.
- **Velocity bins**: 2.02 m/s sawtooth, 1.01 triangle or TDM, 0.5 triangle + TDM.
- **False alarms**: CFAR at 1e-6 over ~90k cells, ~0.1 per CPI; clutter residue near
  v = 0; four fixed spurs at 56, 168, 337 and 360 m (`docs/rf_and_regulatory.md`, "Spurs
  riding the chirp"), fixed r with a nonzero v that differs per mode.
- **Timing**: 6-30 CPIs/s, irregular (the rate falls with detections and fake targets).
  TDM doubles the CPI: ~18 CPIs/s at best, ~9 with triangle + TDM (from the K1 and K10
  timings, not measured). The 6.5 delays stretch with it (walker ~1.9 s at 15 CPIs/s), and
  L5 runs at these rates: test it there, not at 25.

## 3. Filter choice

### 3.1 Nonlinear is not the same as manoeuvring

| Question | Means | Tools |
|---|---|---|
| Is the model nonlinear? | f(x) or h(x) is not a matrix times x | KF -> EKF / UKF -> particle filter |
| Does the target change behaviour? | the motion model is sometimes wrong | Q (`sa`) -> adaptive Q -> IMM |

A target that stops, starts or brakes is still linear: constant velocity plus random
acceleration. Q carries the random acceleration; `sa` says how hard targets may manoeuvre.

### 3.2 The choices

- **(r, v): linear Kalman filter.** F is linear and H = I (both measured), so the KF is
  optimal; an EKF or UKF would reduce to the same equations. Even the sawtooth
  range-Doppler coupling is linear (6.3).
- **(x, y): EKF.** Constant velocity in Cartesian coordinates is linear; the measurement
  (r, theta, v_r) is not. TI's gtrack uses this EKF, and its Jacobian is 12 closed-form
  terms (9.1). Not a polar state: a straight-line target is not constant-velocity in polar
  coordinates.
- **No particle filter**: it pays only for multi-modal or strongly non-Gaussian
  uncertainty (angle-only tracking, track-before-detect).
- **UKF, adaptive Q, IMM**: only on evidence (Section 10).

### 3.3 Measured v is the advantage

Constant-velocity filter, steady state under 8 m/s^2 braking, 25 CPI/s, `sr` 0.7 m,
`sv` 0.5 m/s (numpy, 2026-11-05):

| Filter | Range lag | v lag | Output sigma_v | Mean NIS (2 = consistent) |
|---|---|---|---|---|
| r and v measured, `sa` 3 | 0.17 m | 1.18 m/s | 0.23 m/s | 9.1 |
| r and v measured, `sa` 5 | 0.06 m | 0.65 m/s | 0.29 m/s | 4.6 |
| r and v measured, `sa` 10 | 0.01 m | 0.27 m/s | 0.37 m/s | 2.6 |
| r only, `sa` 3 | 1.76 m | 5.31 m/s | 0.49 m/s | - |

- The measured v cuts the lag about tenfold: a braking car shows its new speed in the next
  CPI.
- At `sa` 3, hard braking puts the mean NIS at the 9.21 gate and the track drops out;
  normal braking (3 m/s^2) gives NIS 3. Raising `sa` costs little noise because v is
  measured. **Default `sa` = 10** (5 until 2026-11-12), tuned by NIS (L0 exercise 1, L6).
- Why 10: the mean NIS hides the tail. On the L2 tracker (30 runs each, 25 CPI/s, Pd 0.9,
  6.5's fail floor in place), share of runs that keep one ID:

  | Scene | `sa` 5 | `sa` 10 |
  |---|---|---|
  | Car braking 8 m/s^2, 15 -> 3 m/s | 67 % | 100 % |
  | Car at 15 m/s passing 30 m off (radial 7.5 m/s^2, 3.4) | 67 % | 100 % |
  | Same, 20 m off (11 m/s^2) | 0 % | 87 % |

  `sa` 15 adds nothing. 6.9's tracker tests and the delay check pass at 10, except (g) at
  seed 0 (see 6.9). A pass 10 m off (22 m/s^2) always loses the ID: accepted, it is inside
  the close-in mask.

### 3.4 Where the (x, y) EKF is weak

- **Sideways motion** is seen only through theta. Cross-range noise is r * sigma_theta:
  3.5 m at 100 m and 10 m at 300 m at 2 deg. Sideways velocity comes from differencing
  positions (the "r only" row above, with ten times the noise), so turns and crossings lag
  and no single `sa` is both smooth and fast. Test (m) in 9.2 measures it.
- **The banana**: at long range the position uncertainty is a curved arc, and the EKF's
  linearization can make the filter overconfident when `r sigma_theta^2 / sigma_r` > ~0.4
  (Lerro and Bar-Shalom, 1993):

  | | 100 m | 300 m |
  |---|---|---|
  | 20 dB SNR (sigma_theta 1.8 deg) | 0.14 | 0.43 |
  | 13 dB SNR (sigma_theta 4 deg) | 0.7 | 2.2 |

  L0 exercise 3 (300 m, 13 dB): the UKF lowers the NEES ~30 %, not enough to switch. The
  EKF stays, with a larger `sa` (9.1).
- **Track start**: v_r gives only the radial part of the velocity; the sideways part starts
  with a large variance. And `v_r = (x vx + y vy) / r` reads (x, vx) and (-x, -vx) alike
  while x is uncertain: near boresight at 300 m and 13 dB a few tracks start on the wrong
  sideways sign and take seconds to recover. No Gaussian filter, EKF or UKF, fixes that.
- The same geometry shows up in (r, v) as apparent radial acceleration: a target at speed
  s passing at distance d has `d2r/dt2 = s^2 d^2 / r^3`, up to `s^2 / r` at closest
  approach (11 m/s^2 for 15 m/s at 20 m). In (r, v) `sa` must absorb it; the Cartesian state
  removes it by construction.

---

## 4. L0 - reading and exercises (no board)

Read:
- [x] Labbe, *Kalman and Bayesian Filters in Python*: 
    - fundamentals
    - EKF, UKF, particle filters, IMM
- [x] TI, *Tracking radar targets with multiple reflection points* (gtrack,
  [PDF](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/1023/Tracking-radar-targets-with-multiple-reflection-points.pdf)):
    - the L5 blueprint
    - EKF on (r, theta, v_r)
    - Tracking via gating + scoring +allocation
- [ ] Bar-Shalom, Li, Kirubarajan, *Estimation with Applications to Tracking and Navigation*:
  (look things up, don't read cover to cover) 
  - Kinematic-models chapter (discrete white-noise acceleration, DWNA: the Q in 6.3, not the continuous CWNA) when writing Q 
  - Consistency of state estimators (NEES/NIS and their chi-square bounds) when tuning 
  - IMM only if Section 10 deems it necessary
- [ ] Blackman, Popoli, *Design and Analysis of Modern Tracking Systems*: look up the SPRT track
  score if false tracks appear.
- Optional: 
  - [ ] *Systematic Analysis of the PMBM, PHD, JPDA and GNN Multi-Target Tracking Filters* ([IEEE](https://ieeexplore.ieee.org/document/9011349/)), the four families side by side; 
  - [ ] *A Survey of Manoeuvring Target Tracking Methods* ([arXiv 1503.07828](https://arxiv.org/pdf/1503.07828)), motion models and IMM.

Tools: 
- FilterPy: 
  - for the exercises 
  - don't use in `common/tracking.py`
- Stone Soup: 
  - ([docs](https://stonesoup.readthedocs.io/)) 
  - Use as a reference: run its JPDA on replayed detections and compare with your GNN. 
  - Metrics: NIS/NEES to tune, OSPA/GOSPA to compare trackers.

Exercises (numpy, an evening each):
1. [x] **NIS**: 
      - constant-velocity KF on one synthetic target
      - r and v measured 
      - Plot NIS over time and check mean is ~2 (the measurement dimension) 
      - Mistune `sa` by 10x each way and watch the NIS and lag
2. [x] **Measured v in association**: 
      - two crossing targets 
      - tracked with and without the v measurement (to see how much v buys)
      - Result: with v no swaps from dv 2 m/s at `sa` 5; r only swaps up to 6-8 times
        (2-3 at matched `sa`). NMS merges first; GNN is enough.
3. [x] **EKF vs UKF**: 
      - constant-velocity target at 300 m in (x, y) 
      - measure as (r, theta, v_r) at 13 and 20 dB SNR (sigma_theta from G6 10.1)
      - Run both filters over Monte Carlo runs and compare the mean NEES (4 for a 4-state filter)
      - This settles whether L5 needs the UKF at long range
      - Result (`ex3_ekf_ukf.py`, random heading): 20 dB both consistent. 13 dB neither in
        the first 5 s (wrong-sign start, 3.4); UKF ~30 % lower NEES, alpha flat; `sa` 10
        consistent after that, with lower RMS. EKF for L5.

Done when the three exercises are run and their results noted.

---

## 5. L1 - SNR per detection (no board)

- Every target dict gains `snr_db`: 
  - the detection cell's power over the CFAR noise estimate (the same cell-averaging estimate the threshold uses), in dB. 
  - Triangle "both": `avg_pwr` over the mean of the two ramps' noise estimates (the down one Doppler-aligned like `avg_pwr`)
  - up-only and down-only on their own ramp's map. 
  - TDM: on `|X_A|^2 + |X_B|^2`. Signal and noise estimate both double, so this is the per-channel SNR the angle-noise formula wants (9.1), not 3 dB above it (Monte Carlo on `cfar_ca_2d`, 2026-11-11).
- `offline/profile_cpi.py` mirrors `process_cpi` and asserts it equal: change both.
- `replay --check` compares whole dicts (`offline/replay.py`, `check()` and `Player`'s per-CPI check): 
  - Problem: A new key fails every older session. 
  - Solution: Compare only the keys the recorded line has: older sessions keep checking exact, and new sessions record `snr_db` and check it too.

Done when:
  - the old reference sessions still pass `replay --check` 
  - a fresh session with `snr_db` passes, 
  - a fake target's `snr_db` moves 20 dB per decade of `amp`.

---

## 6. L2 - tracker in (r, v) (no board)

### 6.1 Conventions

- **Sign**: `TargetSim` moves `r = r0 + v0*t` and `test_target_sim.py` asserts `process_cpi`
  reports `v0` with that sign. So **v > 0 is receding** and `dr/dt = +v`. Confirm on the
  first real recording: an approaching car must read v < 0.
- **Time**: dt from the CPI timestamps (live: monotonic at capture return; replay and
  playback: the recorded `index.csv` time), never a nominal frame period.

### 6.2 Filter interface

At the filter level, not the matrix level, so a KF, an EKF, a UKF and an IMM all plug in
without touching association or track management:

```
class TrackFilter:                 # one instance per track
    __init__(cfg), init(det)       # a track from one detection
    predict(dt)
    innovation(det) -> (y, S)      # y wrapped on circular axes
    update(det)
    state, cov                     # for the GUI and the tests

    # Batched classmethods, one pass over all tracks; the Tracker calls only these (6.8)
    predict_batch(filters, dt)
    update_batch(filters, dets) -> (v, r, nis)  # one det per filter: v unrolled, r decoupled
    d2_matrix(filters, dets) -> d2 (n_f, n_d)   # gating
    pair_d2(filters) -> d2 (n, n)               # duplicates
```

`predict` and `update` are one-filter calls of the batched versions, so the maths has one
code path and the filter tests run what the tracker runs.

The model is chosen once from the config: 
- `TDM_EN` = True -> EKF (or UKF) 
- `TDM_EN` = False -> KF 

One session has one config, so nothing switches mid-run. Until L5 exists, `TDM_EN` also gets the (r, v) KF and theta is ignored.

### 6.3 The (r, v) Kalman filter

```
# state variable
x = [r, v]

# process model
F = [[1, dt], 
     [0, 1]]

# discrete white-noise acceleration (DWNA, piecewise constant; "Why DWNA" below)
Q = sa^2 * [[dt^4/4, dt^3/2], 
            [dt^3/2, dt^2]]

# measurement 
z = [r, v]  
R = diag(sr^2, sv^2),  

# a track starts from one detection
P0 = R          

# kind: sawtooth detections are all "up"; triangle gives "both" (the coupling
# cancels) plus up-only ("up") and down-only ("down")
H by kind:  "up"   [[1,  k], 
                    [0, 1]]                 
            "down" [[1, -k], 
                    [0, 1]]                 
            "both" I 
where k = CHIRP_FC_HZ * T_EFF / CHIRP_BW_HZ   # ~0.012 s, dsp.md "Range-Doppler coupling"
```

- The v innovation is wrapped into [-`MAX_VELOCITY`, `MAX_VELOCITY`): the Doppler axis is
  circular (`dsp.md`). It only matters near the edge.
- Defaults: 
    - `sr` = 0.7 m (sub-bin refined, bins are 3.0 m)
    - `sv` = 0.5 m/s
    - `sa` = 10 m/s^2 (3.3)
    - Fixed R to start; scaling it per detection from `snr_db` is an upgrade (Section 10).
    - `sr` and `sv` cover measurement noise only. On a car the strongest reflection wanders
      over the body (gtrack sizes its car gate for +-4 m, +-2 m/s), so expect L6a to need a
      larger `sr` for cars (Section 10).
- **Why DWNA**: Labbe and gtrack (2.3) recommend the continuous model (CWNA,
  `Q = q * [[dt^3/3, dt^2/2], [dt^2/2, dt]]`) for irregular dt. Measured 2026-11-06, 8 m/s^2
  braking, CWNA's `q` matched to `sa` 5 at 25 CPI/s, mean NIS:

  | CPI rate | DWNA | CWNA |
  |---|---|---|
  | 25/s | 4.4 | 4.4 |
  | 6/s | 3.9 | 12.3 |
  | 20-50 ms jitter | 4.2 | 4.1 |

  A manoeuvre's prediction error grows as `a*dt`, and so does DWNA's noise (`sa*dt`); CWNA's
  grows as `sqrt(dt)` and falls behind at low CPI rates. CWNA fits white-noise truth, not
  sustained braking or turns.

### 6.4 Association

- **Gate**: Mahalanobis `d^2 = y' S^-1 y < 9.21` (chi-square, 2 dof, 99 %).
- **Two-stage GNN**, each stage `scipy.optimize.linear_sum_assignment` on d^2 with
  out-of-gate pairs at a large cost (and dropped after assignment):
  1. confirmed tracks against all detections;
  2. tentative tracks against the detections left over;
  3. what is left starts tentative tracks, **except within a confirmed track's NMS window**
     (+-3 range and +-3 velocity bins around its prediction). A walker's second dot sits
     1-2 velocity bins off, at d^2 12-48: outside the gate, inside the window.

  Why two stages: a fresh tentative track has a large S, so its d^2 to everything is small,
  and in one stage it steals detections from confirmed tracks. (Textbook alternative: cost
  `d^2 + ln|S|`.)
- Triangle: every `kind` is accepted, each with its own H.
- **Accepted limit**: two targets in one NMS window give one detection, so one track (two
  people walking side by side). Angle cannot split them either: two virtual elements give
  one blended angle per cell (G6 10.1).

### 6.5 Track management

- **Confirm**:
  - M of N CPIs hit (3 of 4) **and** the kinematic check has passed. 
  - Tentative tracks are not shown (L4 has a toggle).
- **Kinematic check (`dr/dt = v`)**, i.e. the spur filter: it rejects any detection stream
  whose range does not move with its v (only spurs are tested, (i)):
  - Over the track's history (the last `W` = `TRACK_KINEMATIC_WINDOW_S`, 3 s) fit the slope of the *measured* r vs t
    (the filter's r follows its own v, so it would always pass).
  - Compare with the mean measured v over the same window, unrolled to the track (6.6). Both are the window's mean velocity, so a constant acceleration cancels exactly. A change of acceleration `da` inside the window does not: it leaves `d ~ 0.03 * da * W` (0.75 m/s for 8 m/s^2 braking that starts mid-window).
  - `d = slope - v_mean`, `sigma_d = sqrt(sigma_slope^2 + sigma_vmean^2)`, with
    `sigma_slope = sr / sqrt(sum((t - t_mean)^2))` (exact for any dt and misses),
    `sigma_vmean = sv / sqrt(N)`, `k = TRACK_KINEMATIC_SIGMA` (3).
    - *Pass*: `sigma_d < |v_mean| / (2k)` and `|d| < k * sigma_d`.
    - *Fail* (delete): `|d| > max(2k * sigma_d, |v_mean| / 2)`, every CPI, confirmed tracks
      too.
    - Neither: wait. Still undecided at age W: pass as stationary (too slow to tell from a
      spur within W).
  - **Why pass at 2k**: a real target has d ~ 0, a spur (fixed r) d ~ v_mean: two bells |v|
    apart, each needs k sigma of room, so the gap must hold 2k sigma. Then a spur passes
    with at most Phi(-k) (k 3: 0.13 %), whatever sr, sv, the CPI rate and v are (Monte
    Carlo over all four, 2026-11-11). Passing once `sigma_slope < |v| / k` let up to 90 % of
    spurs through (~50 % at the defaults); leaving sigma_vmean out of the condition did too
    for large sv.
  - **Why fail at 2k, not k**: the check runs every CPI for the track's whole life. Failing
    at k sigma deleted ~80 % of real tracks within 2 min; at 2k none (2 Phi(-2k) per CPI).
    It costs nothing on spurs: their d ~ |v| crosses 2k sigma_d when they become decisive.
  - **Why the `|v_mean| / 2` floor** (2026-11-12): sigma_d assumes white noise and shrinks
    to ~0.1 m/s over W, so the bare 2k rule fails at 0.66 m/s. A car's strongest reflection
    wanders over the body (correlated range noise), and braking or a pass leaves the `da`
    term above: real confirmed tracks were deleted. A spur has d ~ |v|, a real target
    d << |v|, so half of |v| splits them whatever the noise colour. Measured on the L2
    tracker, 30 runs each, confirmed IDs per target (1 = ideal):

    | Scene | 2k only | With the floor |
    |---|---|---|
    | Car 10 m/s, 60 s, range wander 1 m rms (1 s correlation) | 10.3 | 1.0 |
    | Same, 2 m rms (2 s), `sa` 10, `sr` 1.5 | 9.0 | 1.0 |
    | Braking 8 m/s^2, `sa` 10 | 2.0 | 1.0 |
    | Passing 20 m off at 15 m/s, `sa` 10 | 2.1 | 1.1 |

    Spurs are unaffected: 6.9 (i) passes, and a track pulled onto a stronger spur by a
    target passing at the spur's v dies as fast (0.2 s mean, 0.9 max). Cost: a stream
    whose range moves at more than half its v no longer fails; a spur does not move.
  - **k alone sets the error rates**; sr, sv and the CPI rate only set the delay T, where
    `sigma_d(T) = |v| / (2k)` and `sigma_d^2 ~= 12 sr^2 / (rate T^3) + sv^2 / (rate T)`.
    At the defaults (25 CPI/s): car 15 m/s ~0.35 s, bike 5 m/s ~0.7 s, walker 1.5 m/s
    ~1.6 s; below ~0.65 m/s nothing decides within W, so 3 s.
  - The pass delay assumes sr and sv match the real noise: L3's NIS (mean 2) checks that.
  - A spur too slow to decide within W passes as stationary; with MTI on, one near v = 0
    sits in the notch and is removed before detection anyway.
- **Delete by time**, not by CPI count:
  - a tentative track after 2 consecutive misses, until it has met M of N;
  - a tentative track that has met M of N (sticky: once met, always met) and only waits
    for the kinematic check: by time,
    like a confirmed one. A walker waits ~1.6 s (40 CPIs), and at Pd 0.9 two misses in a row
    somewhere in 40 CPIs happen ~30 % of the time;
  - a confirmed track after `TRACK_DELETE_S` (0.5 s) without an update;
  - a confirmed track with |v| under one velocity bin while MTI is on: after
    `TRACK_STOP_COAST_S` (5 s), coasting with v set to 0 (a stopped target should not
    drift). A person who stops keeps their ID for 5 s, then gets a new one. One bin is
    enough: MTI drops a walker at 0.5-1.5 m/s (by SNR), inside the 2.02 m/s bin.
    Checked on the L2 tracker (2026-11-12, 30 runs of 60 s each):
    - Zeroing v on a miss does not shake off a slow walker that is still detected:
      1.2, 1.5 and 1.8 m/s, MTI on, sawtooth and TDM bins, one ID in every run.
    - Capping P while it coasts (sigma_v <= one bin, sigma_r <= bin x coast time) changes
      nothing: the 4 s stop keeps the ID in 35 of 40 seeds with and without it (32 and 34
      at `sa` 10). Not worth adding.
  The tracker therefore needs each CPI's MTI state.
- **Duplicates**: two confirmed tracks within the gate of each other for `TRACK_MERGE_N`
  CPIs (10), measured as d^2 between their predicted measurements with `S = S_a + S_b`:
  delete the younger.
- **Cap**: `TRACK_MAX` (100) tracks; at the cap no new tentative tracks start (logged
  once). It protects the CPU when MTI is off in clutter (6.8).
- **`TRACK_MIN_SPEED`** (0 = off): minimum |v| to start a track, for parked-car scenes
  with MTI off.
- **Accepted: a slow crossing loses one ID.** Two targets inside one NMS window merge into
  one detection for `18 m / dv`: ~3.6 s for two cars at dv 5 m/s, ~36 s for one walker
  overtaking another at 0.5 m/s. The track without detections coasts out and the target
  returns under a new ID. Coasting for seconds to save the ID would cause more wrong
  reassociations than it fixes.
- IDs are increasing integers, never reused within a run.

### 6.6 Placement

- `common/tracking.py`: 
  - pure numpy/scipy, no Qt.
  - `Tracker(cfg).update(detections, t, mti_en) -> list[Track]`, all tracks; 
  - the GUI filters by status.
  - no FilterPy
- `Track`: 
  - Content: id, status (tentative / confirmed), filter (state, covariance), hits, misses,
  time of the last update, age, the last detection's `snr_db` (for the track list), a W
  (3 s, trimmed by time) history of measured `(t, r, v)` (plus `x, y` in L5) for the
  kinematic check and the ~2 s tails,
  with v unrolled to the track (predicted v + wrapped innovation, so a track that speeds
  past `MAX_VELOCITY` keeps passing the check), and the last update's NIS (written each CPI
  by L3's `replay`).
  "Coasting" = confirmed and not updated this CPI.
  - Size: the history is a `deque(maxlen=...)`, ~19 kB per track as Python tuples (90
    entries of 5 floats, measured), ~2 MB at `TRACK_MAX`. Nothing grows with track age.


### 6.7 Config (group `tracking`; the GUI rows come for free)

| Field | Default | Unit |
|---|---|---|
| `TRACK_EN` | True | |
| `TRACK_STD_A` | 10 | m/s^2 |
| `TRACK_STD_THETA_MIN_DEG` (L5, 9.1) | 0.5 | deg |
| `TRACK_STD_A_XY` | 10 | m/s^2 |
| `TRACK_STD_R` | 0.7 | m |
| `TRACK_STD_V` | 0.5 | m/s |
| `TRACK_CONFIRM_M` / `TRACK_CONFIRM_N` | 3 / 4 | CPIs |
| `TRACK_KINEMATIC_SIGMA` | 3 | sigma |
| `TRACK_KINEMATIC_WINDOW_S` (W) | 3 | s |
| `TRACK_KINEMATIC_WINDOW_S` | 3 | s |
| `TRACK_DELETE_S` | 0.5 | s |
| `TRACK_STOP_COAST_S` | 5 | s |
| `TRACK_MERGE_N` | 10 | CPIs |
| `TRACK_MAX` | 100 | tracks |
| `TRACK_MIN_SPEED` | 0 | m/s |

The gates (9.21, 11.34) are chi-square constants, not config.

As built on 2026-11-12, `config.py` differs in two defaults: `TRACK_EN` False (until L3
wires the tracker) and `TRACK_STD_A` 5 (3.3's change is not in yet).

### 6.8 Cost

CPI budget ~33 ms, of which `process_cpi` takes ~25: the tracker gets a few ms at most.
Measured 2026-11-12 on the L2 tracker, 98 confirmed tracks, Pd 0.9, 2 false alarms per CPI:

| Version | ms per CPI |
|---|---|
| Per-track Python loop | ~10 (kinematic refit ~1/3, 2x2 numpy calls ~1/2) |
| Batched over tracks + kinematic check on running sums | **~1.5**, identical output |

- Batched: the classmethods of 6.2, one numpy pass for all tracks.
- Running sums: `[n, t, t^2, r, t*r, v]` per track, t relative to its birth, added on
  append and subtracted on drop: O(1) per CPI. Drift over a 24 h track: 1e-5 m/s.
- What is left is gathering x and P from the tracks ~6x per CPI; a struct of arrays
  would remove it if ever needed.
- L5's EKF must batch the same way: in a per-track loop it measured ~45 us per track
  (2026-11-05), ~5 ms at `TRACK_MAX` before track management.

### 6.9 Tests (`tests/test_tracking.py`, `__main__` asserts)

Synthesize detection streams from ground-truth kinematics with a seeded Generator:
measurement noise at (`sr`, `sv`), Pd = 0.9, ~2 false alarms per CPI uniform over the map,
dt jittered over 20-50 ms.

- (a) One target: one confirmed track, the same ID for 20 s, RMS range error below `sr`.
- (b) Two targets crossing in range, more than 3 velocity bins apart: the IDs do not swap.
- (c) False alarms only: no confirmed track (or fewer than one per minute).
- (d) The target vanishes: its track is deleted within `TRACK_DELETE_S`.
- (e) Fixed vs jittered dt: the same tracks.
- (f) Slow crossing, under 3 bins apart, with the generator emitting one detection (the
  stronger) while both targets share a 7x7 window: no swap; at most one ID lost.
- (g) Stop and go, MTI on: a walker stops and its detections cease while |v| is in the
  notch. 4 s: same ID. 6 s: new ID.
- (h) Second dot: a walker with an extra detection 1-2 velocity bins off in 30 % of CPIs:
  one confirmed track.
- (i) Spurs: fixed r with v = 2, 10 and 40 m/s at Pd 1: never confirmed. Then the
  reference sessions (`data/sessions/20261102-19*`, real RF, all four spurs) through
  the tracker: no confirmed track sits at a spur range for 1 s (fake targets pass through
  them, a spur stays while its v says it moves). They live on the laptop, so this
  part skips with a message where they are absent.
- (j) Consistency: one target whose truth has a random acceleration drawn from
  N(0, `sa`^2) each CPI and held over it (the DWNA model): mean NIS inside the 95 %
  chi-square band for 2 dof.
- (k) Triangle kinds: a mix of "up", "down" and "both" from one target: one track, and its
  range has no `k*v` bias.
- Confirmation delay: a walker at 1.5 m/s and a car at 15 m/s confirm within 1.25x the 6.5
  delay T computed from the config (median; p90 within 2x). At the defaults ~1.6 s and ~0.35 s.

Field cases (added 2026-11-12; (a)-(k) use white noise and gentle motion). Each over ~30
seeds, as a share of runs:
- (p) Hard braking: a car at 15 m/s brakes at 8 m/s^2 to 3 m/s: one ID in >= 90 %.
- (q) Passing: a car at 15 m/s on a straight line 30 m off boresight, in (r, v):
  one ID in >= 90 %.
- (r) Coloured range noise: a car at 10 m/s for 60 s with an AR(1) range error of 1 m rms,
  1 s correlation, on top of `sr`: one ID in >= 90 %.
- (s) Spur capture: a target passes a fixed-r stream of its own v (+-0.5 m/s) that is
  stronger, with one detection while both share a 7x7 window: no confirmed track sits on
  the spur for 1 s after the target has left.
- (g) is seed-fragile: at 2 false alarms per CPI the 4 s stop keeps the ID in 35 of 40
  seeds at `sa` 5 and 32 of 40 at 10 (38 and 40 at 0.1 per CPI), and seed 0 fails at 10.
  Assert a share (>= 70 %), not one seed.

Done when (a)-(k), (p)-(s) and the delay check pass.

---

## 7. L3 - app, playback, replay

- **Worker**: owns the tracker (state across CPIs), calls
  `tracker.update(targets, t_cpi, mti_en)` right after `process_rx_data` with the CPI's
  capture timestamp (the one the recorder writes to `index.csv`, so replay reproduces it)
  and MTI state, and rebuilds it on RE-CONFIGURE (including the revert).
  A fake-target edit does not rebuild it: old tracks coast out.
- **New signal `tracks`**: snapshots (plain dicts or copies), never the live `Track`
  objects, which the worker keeps mutating. `results` is unchanged. With `TRACK_EN` off
  there is no tracker and no signal.
- **Playback**: `PlaybackWorker` owns its own tracker, runs it on the recorded times and
  emits `tracks`. A seek resets the tracker, which then warms up again from the new
  position.
- **Replay**: the default output is one JSON line per CPI with the tracks, including the NIS, for
  offline tuning (L6). `--track` runs the tracker on a session recorded without it, and
  `--set TRACK_X=V` (repeatable) overrides tracking fields only, so the detections stay
  exact. `--check` stays detections-only.

Done when each loopback fake target gives one stable track live, `replay` output is
deterministic (two runs, identical output), and the fps cost is measured against the K1
table in `TODO.md`.

---

## 8. L4 - GUI (layout A)

**Radar tab**
- One toolbar row: MTI, Detections, Tentative, Up/Down, Record and the notes field. It
  replaces the checkboxes under the plots.
- Left, ~60 %: the Sector-PPI. With TDM off, the range-velocity plot takes this slot and
  the right column holds only the track list.
- Right top: the range-velocity plot, tracks and detections. In playback the magenta rings
  for recorded detections stay here.
- Right bottom: the track list. Confirmed tracks only, sorted by ID so rows do not jump.
  Columns: ID, r, v, az (TDM only), SNR, age. The current RCS column goes: nothing fills it.

**Tracks on the plots**: appear automatically once confirmed, as an ID label with a ~2 s
tail, in green. Coasting tracks are dimmed (e.g. a person stopped in the MTI notch).
Tentative tracks are hidden unless the toggle is on.

**Detections**: faint dots fading over ~1 s; the Detections toggle hides them.

**Selecting**: click a track in either plot or in the list. It turns yellow with a ring in
all three and stands out in the History tab.

**Other tabs**
- Signals: the up and down RD maps on top, the spectrogram(s) below (one in fabric mode).
- History: track lines over the range-vs-time and velocity-vs-time images.

K1 measured the GUI slot at ~5 ms with 20 detections, in the GUI thread. Measure it again
with tracks and tails; throttle like the History images (<= 5 Hz) if needed.

Done when live and playback show the layout with fake targets tracked in loopback.

---

## 9. L5 - tracker in (x, y) (no board to build it)

### 9.1 Model

- State `[x, y, vx, vy]`, constant velocity. `x = r sin(theta)`, `y = r cos(theta)`: the
  radar at the origin, y along boresight, theta > 0 towards antenna B (`CLAUDE.md`).
- Q: the 6.3 block on (x, vx) and on (y, vy), with `TRACK_STD_A_XY` (10, from L0
  exercise 3: it covers the linearization error). The Cartesian state removes the passing
  geometry that set the (r, v) value (3.3), so tune the two apart in L6.
- Units: `theta` in the target dicts is in degrees; the filter works in radians.
- Measurement and Jacobian:

  ```
  h(x) = [ r = sqrt(x^2 + y^2),  theta = atan2(x, y),  v_r = (x vx + y vy) / r ]
  H = [ [ x/r,                 y/r,                 0,   0   ],
        [ y/r^2,              -x/r^2,               0,   0   ],
        [ y(vx y - vy x)/r^3,  x(vy x - vx y)/r^3,  x/r, y/r ] ]
  ```

- R per detection: `diag(sr^2, sigma_theta^2, sv^2)`, with
  `sigma_theta = 1 / (pi cos(theta) sqrt(SNR_ch))` rad (G6 10.1) and
  `SNR_ch` = `snr_db` (5). Triangle + TDM "both" sums two looks (up and down phasors):
  sigma_theta / sqrt(2). Floor sigma_theta at `TRACK_STD_THETA_MIN_DEG` (0.5 deg) for
  calibration error.
  - Clamp `cos(theta)` at 0.5: theta is the measured one, and near +-90 deg (spurs read
    60-75 deg) R would blow up.
  - 0.5 deg suits fake targets. On air expect 1-2 deg: psi0 is set by walking a reflector
    (G6 10.2), the turntable curve leaves a residual, and a near car is wider than the
    thermal sigma (4.5 m at 30 m is 8 deg). L6b sets it by NIS.
- Innovations: theta wrapped to [-pi, pi), v_r wrapped as in 6.3. The range row carries
  the per-kind coupling of 6.3 (`r + k v_r` for "up").
- Start from one detection: position from (r, theta); velocity = v_r along the line of
  sight, sigma `sv`; sideways velocity unknown, sigma ~10 m/s; rotate that covariance into
  (x, y).
- Gate 11.34 (chi-square, 3 dof, 99 %). Association, track management and the kinematic
  check (on r and v_r) are unchanged.
- **Stop-coast changes** (6.5 sets v to 0 below one bin with MTI on). The notch is on the
  *radial* speed, and in (x, y) a target can sit in it while moving: a walker at 1.5 m/s
  has |v_r| < 0.5 m/s whenever its heading is within ~20 deg of sideways, for as long as
  that lasts (~14 s around closest approach on a straight pass 30 m off). So: if the
  track's speed `hypot(vx, vy)` is under one bin, it stopped: zero the velocity. Otherwise
  it is crossing: coast on its velocity for `TRACK_STOP_COAST_S`. A sideways walker still
  outlasts 5 s and comes back under a new ID: accepted, it is what MTI costs. Test (o)
  with MTI on shows it.

### 9.2 Tests (same file)

- (l) A straight-line target at 100 m, 20 dB: mean NEES <= ~4 over Monte Carlo runs
  (under 4 is the price of `sa` 10).
- (m) **Turn**: a car at 15 m/s turning 90 deg at 100 m. Report the sideways lag and the
  NIS through the turn. This test decides adaptive Q, a CA model or an IMM (Section 10).
- (n) **Long range**: 300 m at 13 dB, random heading (`ex3_ekf_ukf.py` is the template):
  mean NEES in the 95 % band after the first 5 s. The start-up bump is expected (3.4).
- (o) A target crossing the beam at 30 m: one track, no ID loss.
  - A car at 15 m/s, MTI off and on: one ID both ways (it is in the notch for ~0.1 s).
  - A walker at 1.5 m/s, MTI on: the ID is lost around closest approach (9.1). Assert
    that the old track coasts on its velocity, not at v = 0, and report the gap.
- Run (l)-(o) at the TDM rate (dt 55-75 ms, ~15 CPIs/s), not at L2's 20-50 ms (Section 2).

Done when (l)-(o) pass, or their failures are understood and resolved through Section 10,
and loopback fake targets with `theta0` track live on the PPI.

---

## 10. Upgrades - only when the data demands them

| Symptom (tests or recordings) | Add |
|---|---|
| NIS spikes at manoeuvres, tracks lag (test m, recordings) | Adaptive Q (Labbe's continuous adjustment: scale Q up when the NIS passes ~4-5, well under the gate) or an IMM (a quiet CV model plus an agile one), or a constant-acceleration (CA) state `[x, y, vx, vy, ax, ay]` (gtrack's default) |
| EKF NEES inconsistent after track start (test n, L6b) | UKF behind the same interface (~30 % lower NEES at 300 m, 13 dB) |
| NIS depends on SNR | R scaled per detection from `snr_db` |
| NIS high on cars, normal on walkers (L6a) | A target-extent term in R (gtrack's dispersion `C_D`, estimated per track) |
| Theta NIS high on near cars, normal far out (L6b) | An extent term in R's theta: `(width / r)^2` added to sigma_theta^2 |
| Slow tracks on trees and bushes in wind (pass as stationary after W) | `TRACK_MIN_SPEED`, then the 2-pulse canceller (`TODO.md`, open small items) |
| L5 tentative tracks (sideways sigma 10 m/s) grab clutter | Cap the gate's size in metres (gtrack's `DepthLimit`, `WidthLimit`) |
| Duplicate tracks on one car or person | NMS Doppler window in m/s instead of bins (TDM doubles micro-Doppler in bins; this changes detections, so `replay --check` on older sessions needs the old setting), then clustering (DBSCAN in (r, v), or gtrack's centroid) |
| Tracks swap when targets crowd | JPDA |
| Many false tracks | SPRT track score (Blackman), or a minimum `snr_db` to start a track (gtrack's `setSNRThre`) |
| Weak far targets never confirm | Track-before-detect (research topic, skip) |

Adaptive Q must trigger before the gate rejects the detection, or the track is lost with
Q unchanged. Clutter associated to a track looks like a manoeuvre and can trigger it too.

---

## 11. Troubleshooting

- **Track IDs swap on crossings** -> gate too wide or `sa` too large. v normally separates
  crossing targets.
- **Tracks drop out during braking** -> `sa` too small: the mean NIS sits near the gate
  (3.3).
- **A car's ID changes every few seconds, walkers hold** -> the kinematic fail on
  non-white range noise (6.5's floor missing), or `sr` too small for cars.
- **Detections fade at the same ranges on every pass** -> the ground bounce, not the
  tracker (Section 12).
- **A second track behind a car, same v, weaker** -> a multipath ghost. It obeys
  `dr/dt = v`, so the kinematic check passes it (Section 12).
- **Walkers take long to confirm** -> compare with the 6.5 delay T: it grows with k, sr and
  sv and with a lower CPI rate.
- **A spur confirms** -> too slow to decide within W (treated as stationary), or `sr`/`sv`
  are smaller than the real noise (mean NIS above 2).
- **Two tracks on one walker** -> the second dot falls outside the gate (Section 10).
- **Velocities mirrored** -> the sign convention (6.1).
- **Angle tracks lag sideways on turns** -> expected (3.4); test (m) and Section 10.
- **Tracks restart after a seek** -> expected: playback resets the tracker.

## 12. L6 - real targets

- **L6a, (r, v), on K6's recordings** (Phase 6 field day 1: cars, people, bikes, a quiet
  stretch; software mode and fabric /8):
  - an approaching car reads v < 0;
  - tune `sa`, `sr`, `sv` by NIS (`replay --track --set TRACK_STD_A=...`): mean NIS ~2 on steady targets, per
    class (cars likely need a larger `sr`, 6.3);
  - count detections per target (one dot or several) against the Section 10 rows;
  - the spurs never confirm;
  - IDs hold in normal traffic; slow crossings lose IDs only as documented in 6.5.
  - Expected, not bugs:
    - **Multipath ghosts confirm.** A bounce off a wall or a second car is a longer path
      with a consistent Doppler: it obeys `dr/dt = v`. Note how often; a fix (ghost =
      same v, longer r, weaker, appears and dies with its parent) is out of scope.
    - **Fades at fixed ranges.** Direct and ground-bounced paths cancel where
      `R = 2 h_radar h_target / (n lambda)`: for 1.5 m and 1.0 m, at 58, 29, 19 m...
      Beyond 58 m there is one last lobe (peak ~116 m), then the R^-8 fall-off (G6 1.3).
      A person spans many heights and blurs it; a car's low reflection points do not.
      K6's range walk (G6 7, step 5) shows the ripple in `snr_db`. A walker can sit in a
      fade longer than `TRACK_DELETE_S`: count those ID losses before raising it.
    - **Slow tracks on vegetation** in wind (Section 10).
  - K6's padded recording (G6 7, step 6) is the SNR field day 2 will have: check that
    confirmation and ID hold survive there, not only at the grid's 30 dBi.
- **L6b, (x, y), on field day 2's recordings** (patches on air, TDM, psi0 set): a person
  walking a known line and a car along a road trace their paths on the PPI; NEES and NIS
  are sane; sideways lag is judged against test (m).

Done when both hold. Phase 7 closes then.

## 13. What moved from Phase 6

K3 became L1-L4; K11's tracker became L5; K11's field day 2 stays in Phase 6 and feeds L6b.
G6 13.3 (tracking reading) became Section 4, and the 2026-09-30 research note
`docs/tracking_algorithms.md` was folded into Sections 2-4 and 10.
