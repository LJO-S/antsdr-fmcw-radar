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
                                 \-> L5 tracker (x, y), fake targets with theta0
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
  22 m/s (lowest: 32 m/s in triangle + TDM), so nothing aliases short of ~115 km/h.
- **Detections per target**: Part G showed mostly one dot per car, person or bike,
  sometimes two for walkers. NMS (`nsize` 3, a 7x7 window, `dsp.py` `nms`) already acts
  as a rough cluster: +-3 range bins (+-9 m) and +-3 velocity bins.
- **Velocity bins**: 2.02 m/s sawtooth, 1.01 triangle or TDM, 0.5 triangle + TDM.
- **False alarms**: CFAR at 1e-6 over ~90k cells, ~0.1 per CPI; clutter residue near
  v = 0; four fixed spurs at 56, 168, 337 and 360 m (`docs/rf_and_regulatory.md`, "Spurs
  riding the chirp"), fixed r with a nonzero v that differs per mode.
- **Timing**: 6-30 CPIs/s, irregular (the rate falls with detections and fake targets).

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
  measured. **Default `sa` = 5**, tuned by NIS (L0 exercise 1, L6).

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

  L0 exercise 3 and test (n) decide whether the UKF is needed.
- **Track start**: v_r gives only the radial part of the velocity; the sideways part starts
  with a large variance.
- The same geometry shows up in (r, v) as apparent radial acceleration: a target at speed
  s passing at distance d has `d2r/dt2 = s^2 d^2 / r^3`, up to `s^2 / r` at closest
  approach (11 m/s^2 for 15 m/s at 20 m). In (r, v) `sa` must absorb it; the Cartesian state
  removes it by construction.

---

## 4. L0 - reading and exercises (no board)

Read:
- Labbe, *Kalman and Bayesian Filters in Python*: **done** (2026-11-05).
- TI, *Tracking radar targets with multiple reflection points* (gtrack,
  [PDF](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/1023/Tracking-radar-targets-with-multiple-reflection-points.pdf)):
  the L5 blueprint, covering the EKF on (r, theta, v_r), gating, scoring and allocation.
- Bar-Shalom, Li, Kirubarajan, *Estimation with Applications to Tracking and Navigation*:
  look things up, don't read cover to cover. The kinematic-models chapter (discretized
  white-noise acceleration, the Q in 6.3) when writing Q; consistency of state estimators
  (NEES/NIS and their chi-square bounds) when tuning; the IMM only if Section 10 calls
  for it.
- Blackman, Popoli, *Design and Analysis of Modern Tracking Systems*: look up the SPRT track
  score if false tracks appear.
- Optional: *Systematic Analysis of the PMBM, PHD, JPDA and GNN Multi-Target Tracking
  Filters* ([IEEE](https://ieeexplore.ieee.org/document/9011349/)), the four families side
  by side; *A Survey of Manoeuvring Target Tracking Methods*
  ([arXiv 1503.07828](https://arxiv.org/pdf/1503.07828)), motion models and IMM.

Tools: FilterPy for the exercises (not in `common/tracking.py`). Stone Soup
([docs](https://stonesoup.readthedocs.io/)) as a reference: run its JPDA on replayed
detections and compare with your GNN. Metrics: NIS/NEES to tune, OSPA/GOSPA to compare
trackers.

Exercises (numpy, an evening each):
1. **NIS**: a constant-velocity KF on one synthetic target, r and v measured. Plot the
   NIS over time and check its mean is ~2 (the measurement dimension). Mistune `sa` by 10x
   each way and watch the NIS and the lag.
2. **Measured v in association**: two crossing targets, tracked with and without the v
   measurement. See how much v buys.
3. **EKF vs UKF**: a constant-velocity target at 300 m in (x, y), measured as
   (r, theta, v_r) at 13 and 20 dB SNR (sigma_theta from G6 10.1). Run both filters over
   Monte Carlo runs and compare the mean NEES (4 for a 4-state filter). This settles
   whether L5 needs the UKF at long range.

Done when the three exercises are run and their results noted.

---

## 5. L1 - SNR per detection (no board)

- Every target dict gains `snr_db`: the detection cell's power over the CFAR noise
  estimate (the same cell-averaging estimate the threshold uses), in dB. Triangle "both":
  on `avg_pwr`; up-only and down-only on their own ramp's map. TDM: on `|X_A|^2 + |X_B|^2`, which is 3 dB above the per-channel SNR that
  the angle-noise formula wants (9.1).
- `offline/profile_cpi.py` mirrors `process_cpi` and asserts it equal: change both.
- `replay --check` compares whole dicts (`offline/replay.py`, `check()` and
  `Player`'s per-CPI check), so a new key fails every older session. Compare only the keys
  the recorded line has: older sessions keep checking exact, and new sessions record
  `snr_db` and check it too.

Done when the reference sessions still pass `replay --check`, a fresh session with
`snr_db` passes it, and a fake target's `snr_db` moves 20 dB per decade of `amp`.

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
    init(det, cfg) -> TrackFilter  # classmethod: a track from one detection
    predict(dt)
    innovation(det) -> (y, S)      # for gating; y wrapped on circular axes
    update(det)
    state, cov                     # for the GUI and the tests
```

The model is chosen once from the config: `TDM_EN` gives L5's EKF, otherwise the (r, v)
KF. One session has one config, so nothing switches mid-run. Until L5 exists, `TDM_EN`
also gets the (r, v) KF and theta is ignored.

### 6.3 The (r, v) Kalman filter

```
x = [r, v]
F = [[1, dt], [0, 1]]
Q = sa^2 * [[dt^4/4, dt^3/2], [dt^3/2, dt^2]]      # discretized white-noise acceleration
z = [r, v],  R = diag(sr^2, sv^2),  P0 = R          # a track starts from one detection
H by kind:  "up"   [[1,  k], [0, 1]]                 # sawtooth detections are "up"
            "down" [[1, -k], [0, 1]]                 # triangle down-only
            "both" I                                 # triangle cancels the coupling
k = CHIRP_FC_HZ * T_EFF / CHIRP_BW_HZ               # ~0.012 s, dsp.md "Range-Doppler coupling"
```

- The v innovation is wrapped into [-`MAX_VELOCITY`, `MAX_VELOCITY`): the Doppler axis is
  circular (`dsp.md`). It only matters near the edge.
- Defaults: `sa` 5 m/s^2 (3.3), `sr` 0.7 m (sub-bin refined, bins are 3.0 m), `sv` 0.5 m/s.
  Fixed R to start; scaling it per detection from `snr_db` is an upgrade (Section 10).

### 6.4 Association

- **Gate**: Mahalanobis `d^2 = y' S^-1 y < 9.21` (chi-square, 2 dof, 99 %).
- **Two-stage GNN**, each stage `scipy.optimize.linear_sum_assignment` on d^2 with
  out-of-gate pairs at a large cost (and dropped after assignment):
  1. confirmed tracks against all detections;
  2. tentative tracks against the detections left over;
  3. what is left starts tentative tracks, **except inside a confirmed track's gate** (a
     walker's second dot).

  Why two stages: a fresh tentative track has a large S, so its d^2 to everything is small,
  and in one stage it steals detections from confirmed tracks. (Textbook alternative: cost
  `d^2 + ln|S|`.)
- Triangle: every `kind` is accepted, each with its own H.
- **Accepted limit**: two targets in one NMS window give one detection, so one track (two
  people walking side by side). Angle cannot split them either: two virtual elements give
  one blended angle per cell (G6 10.1).

### 6.5 Track management

- **Confirm** when M of N CPIs hit (3 of 4) **and** the kinematic check has passed. Tentative
  tracks are not shown (L4 has a toggle).
- **Kinematic check (`dr/dt = v`)**, the spur filter. Over the track's history (up to the
  last ~3 s), fit the slope of r against t and compare it with the mean measured v over the
  same window. Both are the window's mean velocity, so manoeuvres do not upset it.
  - *Decisive* once the slope's uncertainty `sigma_slope` (from the fit; roughly
    `sr * sqrt(12 / (N T^2))` for N updates over T seconds) is below `|v_mean| / k`, with
    k = `TRACK_CHECK_K` (3). A track with |v_mean| under `TRACK_CHECK_VMIN` (0.7 m/s) is
    decisive at once: a stationary target has nothing to check, and a slower target would
    need more than the window to decide.
  - *Pass* when `|slope - v_mean| < k * sqrt(sigma_slope^2 + sigma_vmean^2)`, with
    `sigma_vmean = sv / sqrt(N)`; *fail* deletes the track.
  - So the confirmation delay scales with speed by itself (25 CPI/s, k 3): a car at 15 m/s
    ~0.2 s, a bike at 5 m/s ~0.4 s, a walker at 1.5 m/s ~1 s, a stationary target at once.
    A spur (fixed r, v != 0) fails the moment the check becomes decisive, so it **never
    confirms**.
  - It keeps running on confirmed tracks over the sliding window: a confirmed track that
    drifts onto a spur and fails is deleted.
  - A spur with |v| under `TRACK_CHECK_VMIN` passes as stationary; with MTI on, one near
    v = 0 sits in the notch and is removed before detection anyway.
- **Delete by time**, not by CPI count:
  - a tentative track after 2 consecutive misses, until it has met M of N;
  - a tentative track that has met M of N and only waits for the kinematic check: by time,
    like a confirmed one. A walker waits ~1 s (25 CPIs), and at Pd 0.9 two misses in a row
    somewhere in 25 CPIs happen ~20 % of the time;
  - a confirmed track after `TRACK_DELETE_S` (0.5 s) without an update;
  - a confirmed track with |v| inside the MTI notch (under one velocity bin) while MTI is
    on: after `TRACK_STOP_COAST_S` (5 s). A person who stops keeps their ID for 5 s, then
    gets a new one.
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

- `common/tracking.py`: pure numpy/scipy, no Qt.
  `Tracker(cfg).update(detections, t, mti_en) -> list[Track]`, all tracks; the GUI filters
  by status.
- `Track`: id, status (tentative / confirmed), filter (state, covariance), hits, misses,
  time of the last update, age, the last detection's `snr_db` (for the track list), a ~3 s
  history of `(t, r, v)` (plus `x, y` in L5) for the kinematic check and the ~2 s tails,
  and the NIS of each update (for tuning, written by L3's `replay --tracks`).
  "Coasting" = confirmed and not updated this CPI.

### 6.7 Config (group `tracking`; the GUI rows come for free)

| Field | Default | Unit |
|---|---|---|
| `TRACK_EN` | True | |
| `TRACK_SIGMA_A` | 5 | m/s^2 |
| `TRACK_SIGMA_R` | 0.7 | m |
| `TRACK_SIGMA_V` | 0.5 | m/s |
| `TRACK_CONFIRM_M` / `TRACK_CONFIRM_N` | 3 / 4 | CPIs |
| `TRACK_CHECK_K` | 3 | sigma |
| `TRACK_CHECK_VMIN` | 0.7 | m/s |
| `TRACK_DELETE_S` | 0.5 | s |
| `TRACK_STOP_COAST_S` | 5 | s |
| `TRACK_MERGE_N` | 10 | CPIs |
| `TRACK_MAX` | 100 | tracks |
| `TRACK_MIN_SPEED` | 0 | m/s |

The gates (9.21, 11.34) are chi-square constants, not config.

### 6.8 Cost

Measured 2026-11-05 with a plain per-track Python loop (predict, gate, assign, update):
~25 us per track for the KF and ~45 us for the EKF, linear in track count (20 tracks:
0.5 / 0.9 ms; 100 tracks: 3 / 5 ms). Track management roughly doubles it. Against a CPI
budget of ~33 ms, of which `process_cpi` takes ~25, a realistic scene (5-20 tracks) costs a
few percent. Vectorizing across tracks would gain ~10x if ever needed.

### 6.9 Tests (`common/test_tracking.py`, `__main__` asserts)

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
  the tracker: no track at a spur range ever confirms. They live on the laptop, so this
  part skips with a message where they are absent.
- (j) Consistency: one target whose truth has white acceleration at `sa`: mean NIS inside
  the 95 % chi-square band for 2 dof.
- (k) Triangle kinds: a mix of "up", "down" and "both" from one target: one track, and its
  range has no `k*v` bias.
- Confirmation delay: a walker at 1.5 m/s confirms within ~1.5 s and a car at 15 m/s within
  ~0.4 s.

Done when (a)-(k) and the delay check pass.

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
- **Replay**: `--tracks <file.jsonl>` writes the tracks per CPI, including the NIS, for
  offline tuning (L6). `--check` stays detections-only.

Done when each loopback fake target gives one stable track live, `replay --tracks` is
deterministic (two runs, identical output), and the fps cost is measured against the K1
table in `TODO.md`.

---

## 8. L4 - GUI (layout A)

Chosen on 2026-11-05 from three mockups.

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
- Q: the 6.3 block with `sa`, on (x, vx) and on (y, vy).
- Measurement and Jacobian:

  ```
  h(x) = [ r = sqrt(x^2 + y^2),  theta = atan2(x, y),  v_r = (x vx + y vy) / r ]
  H = [ [ x/r,                 y/r,                 0,   0   ],
        [ y/r^2,              -x/r^2,               0,   0   ],
        [ y(vx y - vy x)/r^3,  x(vy x - vx y)/r^3,  x/r, y/r ] ]
  ```

- R per detection: `diag(sr^2, sigma_theta^2, sv^2)`, with
  `sigma_theta = 1 / (pi cos(theta) sqrt(SNR_ch))` rad (G6 10.1) and
  `SNR_ch` = `snr_db` - 3 dB (5), floored at ~0.5 deg for calibration error.
- Innovations: theta wrapped to [-pi, pi), v_r wrapped as in 6.3. The range row carries
  the per-kind coupling of 6.3 (`r + k v_r` for "up").
- Start from one detection: position from (r, theta); velocity = v_r along the line of
  sight, sigma `sv`; sideways velocity unknown, sigma ~10 m/s; rotate that covariance into
  (x, y).
- Gate 11.34 (chi-square, 3 dof, 99 %). Association, track management and the kinematic
  check (on r and v_r) are unchanged.

### 9.2 Tests (same file)

- (l) A straight-line target at 100 m, 20 dB: mean NEES ~4 over Monte Carlo runs.
- (m) **Turn**: a car at 15 m/s turning 90 deg at 100 m. Report the sideways lag and the
  NIS through the turn. This test decides adaptive Q or an IMM (Section 10).
- (n) **Long range**: 300 m at 13 dB, NEES. If inconsistent, the UKF (L0 exercise 3)
  replaces the EKF behind the interface.
- (o) A target crossing the beam at 30 m: one track, no ID loss.

Done when (l)-(o) pass, or their failures are understood and resolved through Section 10,
and loopback fake targets with `theta0` track live on the PPI.

---

## 10. Upgrades - only when the data demands them

| Symptom (tests or recordings) | Add |
|---|---|
| NIS spikes at manoeuvres, tracks lag (test m, recordings) | Adaptive Q (Labbe's continuous adjustment: scale Q up when the NIS passes ~4-5, well under the gate) or an IMM (a quiet CV model plus an agile one) |
| EKF NEES inconsistent at long range or low SNR | UKF behind the same interface |
| NIS depends on SNR | R scaled per detection from `snr_db` |
| Duplicate tracks on one car or person | NMS Doppler window in m/s instead of bins (TDM doubles micro-Doppler in bins; this changes detections, so `replay --check` on older sessions needs the old setting), then clustering (DBSCAN in (r, v), or gtrack's centroid) |
| Tracks swap when targets crowd | JPDA |
| Many false tracks | SPRT track score (Blackman) |
| Weak far targets never confirm | Track-before-detect (research topic, skip) |

Adaptive Q must trigger before the gate rejects the detection, or the track is lost with
Q unchanged. Clutter associated to a track looks like a manoeuvre and can trigger it too.

---

## 11. Troubleshooting

- **Track IDs swap on crossings** -> gate too wide or `sa` too large. v normally separates
  crossing targets.
- **Tracks drop out during braking** -> `sa` too small: the mean NIS sits near the gate
  (3.3).
- **Walkers take long to confirm** -> `TRACK_CHECK_K` too strict, or `sr` too small for the
  slope fit.
- **A spur confirms** -> its |v| is under `TRACK_CHECK_VMIN` (treated as stationary), or
  the check's window is too short.
- **Two tracks on one walker** -> the second dot falls outside the gate (Section 10).
- **Velocities mirrored** -> the sign convention (6.1).
- **Angle tracks lag sideways on turns** -> expected (3.4); test (m) and Section 10.
- **Tracks restart after a seek** -> expected: playback resets the tracker.

## 12. L6 - real targets

- **L6a, (r, v), on K6's recordings** (Phase 6 field day 1: cars, people, bikes, a quiet
  stretch; software mode and fabric /8):
  - an approaching car reads v < 0;
  - tune `sa`, `sr`, `sv` by NIS (`replay --tracks`): mean NIS ~2 on steady targets;
  - count detections per target (one dot or several) against the Section 10 rows;
  - the spurs, and any multipath ghosts, never confirm;
  - IDs hold in normal traffic; slow crossings lose IDs only as documented in 6.5.
- **L6b, (x, y), on field day 2's recordings** (patches on air, TDM, psi0 set): a person
  walking a known line and a car along a road trace their paths on the PPI; NEES and NIS
  are sane; sideways lag is judged against test (m).

Done when both hold. Phase 7 closes then.

## 13. What moved from Phase 6

K3 became L1-L4; K11's tracker became L5; K11's field day 2 stays in Phase 6 and feeds L6b.
G6 13.3 (tracking reading) became Section 4, and the 2026-09-30 research note
`docs/tracking_algorithms.md` was folded into Sections 2-4 and 10.
