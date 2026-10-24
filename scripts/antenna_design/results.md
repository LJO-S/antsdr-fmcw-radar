# openEMS simulations

## Phase 6 K8

Note (2026-10-04): each run below is re-designed for its own eps_r (L, W, inset and line
width all change), so all three resonate near 5.8 GHz. They are not the calibration
curve; they are ready-made redesigns for once the board's real eps_r is known. The
calibration curve is the eps_r 4.4 geometry, fixed, simulated on eps_r 4.2-4.6:
`eps_r_sweep.md` (from `sweep_eps_r.py`). The 4.4 design is the one to fabricate.

Update (2026-10-05): the three runs below use h 1.6 mm, the finished board thickness.
JLCPCB's 2-layer "1.6 mm" is a 1.5 mm core plus copper, so the 4.4 design was re-tuned at
h 1.5 mm (last section). That one is the board to fabricate, and the sweep uses it.

Single patch iterations:

#### eps_R = 4.2
==== KiCad summary, eps_r 4.20 ====
Stackup   FR4, eps_r 4.20, tan d 0.020, dielectric h 1.600 mm. Sim copper has zero thickness and no solder mask: open the mask over all F.Cu.
Edge.Cuts 52.133 mm (x, along the feed) x 56.028 mm (y). B.Cu: solid ground over the whole board.
Origin    board edge at the feed, line centre; x into the board
F.Cu      feed line   width 3.167 mm, x = 0 to 23.730 mm (feed point)
          patch       L 12.133 mm (x, resonant) x W 16.028 mm (y), x = 20.000 to 32.133 mm
          notch       depth 3.730 mm, gap 1.000 mm each side (cut-out 5.167 mm wide)
F.Cu polygon (patch + notch + line), mm:
   1  (  0.000,  -1.583)
   2  ( 23.730,  -1.583)
   3  ( 23.730,  -2.583)
   4  ( 20.000,  -2.583)
   5  ( 20.000,  -8.014)
   6  ( 32.133,  -8.014)
   7  ( 32.133,   8.014)
   8  ( 20.000,   8.014)
   9  ( 20.000,   2.583)
  10  ( 23.730,   2.583)
  11  ( 23.730,   1.583)
  12  (  0.000,   1.583)
Simulated S11 min -29.0 dB at 5.790 GHz; -10 dB band 5.680-5.900 GHz; worst over 5.75-5.85 GHz -15.4 dB
          line 53.1 Ohm; Dmax 8.0 dBi


#### eps_R = 4.4
==== KiCad summary, eps_r 4.40 ====
Stackup   FR4, eps_r 4.40, tan d 0.020, dielectric h 1.600 mm. Sim copper has zero thickness and no solder mask: open the mask over all F.Cu.
Edge.Cuts 51.808 mm (x, along the feed) x 55.728 mm (y). B.Cu: solid ground over the whole board.
Origin    board edge at the feed, line centre; x into the board
F.Cu      feed line   width 3.059 mm, x = 0 to 23.690 mm (feed point)
          patch       L 11.808 mm (x, resonant) x W 15.728 mm (y), x = 20.000 to 31.808 mm
          notch       depth 3.690 mm, gap 1.000 mm each side (cut-out 5.059 mm wide)
F.Cu polygon (patch + notch + line), mm:
   1  (  0.000,  -1.529)
   2  ( 23.690,  -1.529)
   3  ( 23.690,  -2.529)
   4  ( 20.000,  -2.529)
   5  ( 20.000,  -7.864)
   6  ( 31.808,  -7.864)
   7  ( 31.808,   7.864)
   8  ( 20.000,   7.864)
   9  ( 20.000,   2.529)
  10  ( 23.690,   2.529)
  11  ( 23.690,   1.529)
  12  (  0.000,   1.529)
Simulated S11 min -34.4 dB at 5.800 GHz; -10 dB band 5.690-5.910 GHz; worst over 5.75-5.85 GHz -16.5 dB
          line 53.1 Ohm; Dmax 8.0 dBi


#### eps_R = 4.6

==== KiCad summary, eps_r 4.60 ====
Stackup   FR4, eps_r 4.60, tan d 0.020, dielectric h 1.600 mm. Sim copper has zero thickness and no solder mask: open the mask over all F.Cu.
Edge.Cuts 51.528 mm (x, along the feed) x 55.445 mm (y). B.Cu: solid ground over the whole board.
Origin    board edge at the feed, line centre; x into the board
F.Cu      feed line   width 2.959 mm, x = 0 to 23.670 mm (feed point)
          patch       L 11.528 mm (x, resonant) x W 15.445 mm (y), x = 20.000 to 31.528 mm
          notch       depth 3.670 mm, gap 1.000 mm each side (cut-out 4.959 mm wide)
F.Cu polygon (patch + notch + line), mm:
   1  (  0.000,  -1.479)
   2  ( 23.670,  -1.479)
   3  ( 23.670,  -2.479)
   4  ( 20.000,  -2.479)
   5  ( 20.000,  -7.722)
   6  ( 31.528,  -7.722)
   7  ( 31.528,   7.722)
   8  ( 20.000,   7.722)
   9  ( 20.000,   2.479)
  10  ( 23.670,   2.479)
  11  ( 23.670,   1.479)
  12  (  0.000,   1.479)
Simulated S11 min -44.7 dB at 5.800 GHz; -10 dB band 5.690-5.910 GHz; worst over 5.75-5.85 GHz -16.9 dB
          line 53.2 Ohm; Dmax 8.0 dBi

#### eps_R = 4.4, h = 1.5 mm - the board to fabricate (2026-10-05)

Re-tuned from the h 1.6 mm design (L 11.808 -> 11.842 mm, inset 3.69 -> 3.70 mm; the
thinner board alone moved it +15 MHz). Two iterations, next hint +0.002 mm.

==== KiCad summary, eps_r 4.40 (geometry for eps_r 4.40) ====
Stackup   FR4, eps_r 4.40, tan d 0.020, dielectric h 1.500 mm. Sim copper has zero thickness and no solder mask: open the mask over all F.Cu.
Edge.Cuts 51.842 mm (x, along the feed) x 55.728 mm (y). B.Cu: solid ground over the whole board.
Origin    board edge at the feed, line centre; x into the board
F.Cu      feed line   width 2.868 mm, x = 0 to 23.700 mm (feed point)
          patch       L 11.842 mm (x, resonant) x W 15.728 mm (y), x = 20.000 to 31.842 mm
          notch       depth 3.700 mm, gap 1.000 mm each side (cut-out 4.868 mm wide)
F.Cu polygon (patch + notch + line), mm:
   1  (  0.000,  -1.434)
   2  ( 23.700,  -1.434)
   3  ( 23.700,  -2.434)
   4  ( 20.000,  -2.434)
   5  ( 20.000,  -7.864)
   6  ( 31.842,  -7.864)
   7  ( 31.842,   7.864)
   8  ( 20.000,   7.864)
   9  ( 20.000,   2.434)
  10  ( 23.700,   2.434)
  11  ( 23.700,   1.434)
  12  (  0.000,   1.434)
Simulated S11 min -33.4 dB at 5.801 GHz; -10 dB band 5.692-5.914 GHz; worst over 5.75-5.85 GHz -16.3 dB
          line 52.9 Ohm; Dmax 8.0 dBi, efficiency 59 %, gain 5.7 dBi

Same geometry, re-run 2026-10-05 (`--refine 1.5`, the sweep's mesh). No new tuning:

- S11 is now at the board edge, where the VNA sees it, instead of at the feed point.
  The 52.9 Ohm line transforms the match: on the original mesh the null went from -58 dB
  at 5.801 GHz to -34 dB at 5.789 GHz.
- Mesh convergence: the null sits at 5.789 / 5.801 / 5.804 GHz at refine 1 / 1.5 / 2, so
  the original mesh was ~15 MHz low, and it happened to cancel the edge shift. The 53 Ohm
  line does not move with the mesh: it is real (dispersion at 5.8 GHz; the quasi-static
  formula gives 50.2 Ohm), not a mesh artifact.
- Efficiency (radiated / accepted) and gain are printed. 59 % matches a Q estimate from
  the bandwidth: Q_t ~ 1/(0.038 sqrt 2) = 18.6, Q_d = 1/tan d = 50, so Q_rad ~ 30 and
  eta = Q_t/Q_rad ~ 62 %. FR4 costs ~2.3 dB of gain per patch.

#### Line pair, predicted (2026-10-09)

`line_pair_sim.py` (refine 1.5, PEC, launches not modelled). At 5.8 GHz, dL 70 mm:

| eps_r | dphi (deg) | eps_eff | lambda_g (mm) | loss (dB/mm) |
|---|---|---|---|---|
| 4.2 | 891.0 | 3.340 | 28.28 | 0.0185 |
| 4.4 | 910.7 | 3.489 | 27.67 | 0.0189 |
| 4.6 | 930.1 | 3.639 | 27.09 | 0.0193 |

- eps_r = 4.4 + (eps_eff - 3.489) / 0.749. `k8_analyze.py` does this lookup.
- Loss above is dielectric only. Smooth copper adds 0.6 dB/m (sim); real 1 oz HASL is
  estimated at 1.1-2.0 dB/m, so expect ~0.020-0.021 dB/mm measured.
- Copper thickness: unresolved. A 35 um strip moves eps_eff +0.001 in the sim, -0.012 by
  the Bahl-Garg formula. Either way <= ~0.02 in eps_r.
- End to end on the simulated eps_r 4.4 pair: `k8_analyze.py` returns eps_r 4.400 (table),
  4.420 (Kirschning-Jansen cross-check), tan d 0.019 (true 0.020).

#### SMA launch (2026-10-09, `sma_launch_sim.py`)

A model of the Molex edge SMA, against the ideal edge port of every table above:

- Patch: the S11 minimum reads ~25 MHz high (+18..+31, mesh-limited), the same over eps_r
  4.2-4.6, so the patch reads ~0.04 eps_r low. `k8_analyze.py` prints the corrected value
  (f_min - 25 MHz, +-10 MHz) next to the raw one; the tables are unchanged.
- Line pair: the launches cancel (eps_r 4.402 vs 4.400). The lines are the better eps_r.
- Launch S11 ~-33 dB in band, ~0.14 dB loss each.

## Measured (2026-10-22, LiteVNA-64)

One patch board, one line pair (JLCPCB FR4 1.6 mm, 1 oz HASL). 5.0-6.3 GHz, 401 points,
SOL + thru + isolation, NanoVNA-Saver. Raw `.s1p/.s2p` kept locally, not in the repo.

| Quantity | Predicted (eps_r 4.4) | Measured |
|---|---|---|
| Patch S11 minimum, raw | ~5.825 GHz (5.801 + 25 MHz launch) | 5.819 GHz, -11.5 dB; -10 dB band 5.61-5.91 |
| Patch S11 minimum, launch removed | 5.801 GHz, -33 dB | **5.678 GHz**, -36 dB; -10 dB band 5.60-5.80 |
| Patch eps_r (launch removed) | 4.4 | **4.61** (flipped line files: 4.61) |
| Line eps_eff at 5.8 GHz | 3.489 | **3.83** (+-0.02 ripple in band) |
| Line eps_r (sim table, extrapolated) | 4.4 | **4.85** (K-J inverted 4.87) |
| lambda_g at 5.8 GHz | 27.67 mm | **26.43 mm** |
| Group index | 1.91 | 2.03 |
| Loss | 0.020-0.021 dB/mm | 0.0195 +- 0.006 dB/mm |
| tan d | 0.02 (assumed) | 0.018-0.019 |
| Line S11 in band (launch) | ~-33 dB (sim) | **-6 to -8 dB** |

- **The line pair is solid.** Flipped lines repeat S21 within +-2 deg and 0.06 dB, S22 = S11,
  and the full 2-port (measured S22) gives the same eps_eff, 3.83. Both the phase and the
  group delay say the long line is slower than predicted, so it is not a phase offset.
- **The launch is far worse than simulated**: |a22| up to 0.4 (sim -33 dB). It moved the patch
  dip +141 MHz and made it shallow and wide; the 25 MHz SMA correction does not cover it. The
  launch comes out of the line pair (`k8_analyze.py`, launch removal). After removal the dip
  looks like the sim. Max |S11| reaches +2 dB off resonance: mostly LiteVNA port 2 (below).
- **TDR** (each line from both ends, far end on the cal-kit load; the patch board; 50 kHz-6.3 GHz, 3201
  points, low-pass step from the `.s1p` with a Hann window): the lines are **45.5 Ohm** along
  their whole length (47.9 predicted at eps_r 4.85, h 1.5). The 45 Ohm "dip" first read as
  ~0.28 pF of solder at the launch was the short line itself: retracted. The four near
  launches overlap within 0.4 Ohm and 2 ps, so the two boards' launches match, as the line
  method needs. The launch mismatch only shows at high frequency, which the Hann-weighted
  TDR (mostly < 3 GHz) cannot resolve: line S11 rises from -25 dB at 1 GHz to -6 dB at
  5.8 GHz (cause open, next bullet). Edge timing, long minus short, gives eps_eff 3.82 +- 0.08 (1-3 GHz):
  the line is slow without port 2 or the eigenvalue method. NanoVNA-Saver's TDR plot
  smooths too much for features this short.
- **The bad launch is unexplained.** It measures like a launch without ground vias
  (`sma_launch_sim.py --kind pair --length 25 --eps-r 4.85 --omit vias` matches 5.0-5.7 GHz;
  the top pads become a quarter-wave stub at 5.75 GHz), but the boards have the
  footprint's two plated holes per SMA (checked 2026-10-24). With them the sim gives -30
  to -40 dB. `TODO.md`, K9 step 0.
- **LiteVNA port 2 is not corrected** (1.5-port): its match, seen through the port-2 cable,
  puts a ~0.3 GHz ripple on the two-port S11 (0.1-0.15 off the load-terminated S11) and on
  S21 (+-0.5 dB). With the load-terminated S11 swapped in: line eps_eff 3.81-3.83, patch
  5.679-5.690 GHz (4.59-4.61), max |S11| after removal +0.6-0.8 dB. The results stand. Clean
  two-port data without port 2: far end on the cal-kit load, open and short, solved for S11,
  S22, S21^2 like a SOL calibration.
- **eps_r: 4.61 from the patch, 4.85 from the lines** - a 0.24 gap, over the 0.1 target.
  Each reading holds on its own. Ruled out:
  - anisotropy: `--eps-xy` moves patch and line by the same 12-13 % of their isotropic
    slopes, so no tensor fits both (fit to the patch, the line reads 3.645);
  - launches (TDR), port 2, fab geometry (calipers: as drawn, cores ~1.49 line pair /
    ~1.54 mm patch);
  - the patch's launch removal (2026-10-24): a different patch launch (+-0.3 pF, +-0.6
    nH, reflections x0-x1.5) gives 4.37-4.65, never 4.85. The patch sim matches Balanis
    within 0.3 %.

  The TDR "same laminate" argument is retracted (the patch feed is too short to read).
  Two fits remain: (a) **something of eps_r ~1.7 touching the lines** makes a 4.6 board
  read eps_eff 3.83 (q 0.73), Z0 and TDR included; a ~2 mm gap would mostly remove it.
  (b) **two laminate panels** (cores 1.49 / 1.54 mm).
- **For K9**: eps_r **4.6** (decided 2026-10-23). Right under (a); under (b) the witness
  patch measures the new panel and fc trims +-0.08 eps_r.

## Phase 6 K9

#### Re-tune at eps_r 4.6 (2026-10-23, the K8 patch)

The board to fabricate. h 1.5, tan d 0.02, PEC copper; same topology, feed, W and
connector width as the baseline below. L by the Balanis length shift, lc by the connector's
lambda_g ratio less 0.1 mm (the tilt trend of the 4.73 / 4.85 runs), transformer
re-synthesized. No trim needed. A lossless check of the same scaling at 4.73 put the
broadside mode at 5.801 GHz.

```
python column_sim.py --eps-r 4.6 --L 11.531 --lc 12.45 --tw 4.481 --tl 6.587 [--refine 1.5]
python make_footprints.py --only column --eps-r 4.6 --L 11.531 --lc 12.45 --tw 4.481 --tl 6.587
```

L 11.531 x W 15.728 mm, connectors 0.6 x 12.45 mm, pitch 23.981 mm, transformer 4.481 x
6.587 mm (37.8 Ohm), feed 2.868 x 20 mm. Board 226.0 mm long (was 230.7).

| | 4.6, refine 1.5 | 4.6, two columns (refine 1) | design on eps_r 4.85 (refine 1) |
|---|---|---|---|
| worst S11, 5.75-5.85 GHz | -18.1 dB | -18.4 dB | -11.8 dB |
| Dmax / efficiency / gain | 14.8 dBi / 40 % / 10.8 dBi | 14.3 / 39 % / 10.3 dBi | 14.5 / 28 % / 9.0 dBi |
| HPBW E / H | 13.4 / 79.4 deg | | 12.5 / 102.9 deg |
| first E-plane sidelobes | -12.8 / -12.8 dB | | -10.8 / -12.6 dB |
| tilt 5.775 / 5.8 / 5.825 GHz | -1.4 / -0.7 / 0.0 deg | -1.4 / -0.7 / +0.1 deg | +3.9 / +5.2 / +6.4 deg |
| coupling S21 | | -24.3 dB | |
| A-B phase vs pi sin(az) | | 4.6 deg rms, +-95.9 at +-30 deg | |

- Same behaviour as the 4.4 baseline; gain 0.3 dB lower (shorter pitch, 0.46 lambda0).
- If the board is really 4.85 (the K8 lines), this design loses 1.8 dB of gain, S11 rises to
  -11.8 dB and the beam tilts +5 deg. The reverse error is milder: a 4.85 design (L 11.216,
  lc 12.05, tw 4.322, tl 6.431; S11 -20.3 dB, 10.6 dBi at refine 1.5) on 4.61 kept -17.7 dB
  and 10.6 dBi, tilt -5 deg.

Below: the 4.4 design, all at eps_r 4.4, h 1.5 mm, tan d 0.02, PEC copper.
Far-field numbers corrected for a phase-centre units bug (fixed in `column_sim.py`).

#### 1x8 column baseline (2026-10-09, refine 1.5)

End-fed, in-line series-fed, uniform. 50 Ohm feed 2.868 x 20 mm, transformer 4.618 x
6.72 mm (37.8 Ohm), 8 patches L 11.80 x W 15.728 mm, connectors 0.6 x 12.80 mm, pitch
24.6 mm. Board 230.7 x 45.7 mm.

- Worst S11 over 5.75-5.85 GHz -16.9 dB.
- Dmax 15.0 dBi, efficiency 40 %, gain 11.0 dBi (FR4 costs ~4 dB).
- HPBW 13.0 deg elevation x ~78 deg azimuth. First sidelobes -13.0 / -13.1 dB.
- Tilt -1.4 / -0.7 / -0.1 deg at 5.775 / 5.800 / 5.825 GHz.
- eps_r 4.3 / 4.5 (not re-tuned): tilt -2.0 / +1.4 deg, S11 -17.1 / -15.0 dB, gain 11.2 / 10.3 dBi.

#### Two columns at lambda/2 (TX board, or RX with one column terminated)

Columns at y = +-12.95 mm (25.9 mm apart), translated copies, A driven, B 50 Ohm.

- Coupling S21 -24.4 dB worst in band. S11 of A -16.7 dB.
- Gain of A 10.5 dBi (0.5 dB below a lone column), embedded azimuth HPBW 90 deg.
- A-B phase vs azimuth over +-60 deg: 4.6 deg rms off pi sin(az) (96 vs 90 deg at +-30 deg),
  about 2 deg of angle error at 30 deg. The turntable calibration (9.5) removes it.

#### Taylor -20 dB taper (learning variant, not for the first board)

- **What**: the centre patches fed harder than the ends (patch width W sets each one's
  share), so the elevation sidelobes drop from -13 to -20 dB for a slightly wider beam.
- **Why not now**: it only shapes elevation; angle (K10) is azimuth. The elevation
  sidelobes hit the ground within ~4 m (1.5 m height), inside the 15 m close-in mask; the
  main beam's ground clutter from ~13 m is MTI's job. It costs 0.6 dB of gain, worse S11
  and 23 dimensions instead of 3, tuned at eps_r 4.4 only. Worth it if field data shows
  returns from above or below the beam past the mask.
- **Sim** (eps_r 4.4, `b5_p6`, refine 1.5): -19.4 / -22.4 dB, 10.4 dBi, worst S11 -11.4 dB
  (no transformer yet). W moves each patch's resonance and phase, so L and each connector
  length are set from the per-patch probes (`--probes`); one shared connector length gave
  an 11 deg phase step and -13 dB. The edge port and feed line radiate at -22..-28 dB re
  the beam (1-3 dB of sidelobe), a real SMA maybe more. Geometry: `--W-list 7.09,8.22,11.56,14.72,15.73,14.17,11.32,9.49
--L-list 12.319,12.215,11.987,11.838,11.800,11.861,12.001,12.116
--lc-list 13.913,13.600,13.214,12.941,12.992,13.372,13.527 --tl 0`.
