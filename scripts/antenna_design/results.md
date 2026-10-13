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

## Phase 6 K9

All at eps_r 4.4, h 1.5 mm, tan d 0.02, PEC copper. Re-tune once K8 gives the real eps_r.
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

Needs per-patch W and L and per-section connector lengths, set from the per-patch
probes (`--probes`): one shared connector length gave an 11 deg phase step at the centre
and -13 dB sidelobes. Trimmed (`b5_p6`, refine 1.5): -19.4 / -22.4 dB, gain 10.4 dBi,
worst S11 -11.4 dB (no transformer yet). The edge port and feed line radiate at
-22..-28 dB re the beam, which costs 1-3 dB at -20 dB sidelobes; whether a real SMA does
too is open. Geometry: `--W-list 7.09,8.22,11.56,14.72,15.73,14.17,11.32,9.49
--L-list 12.319,12.215,11.987,11.838,11.800,11.861,12.001,12.116
--lc-list 13.913,13.600,13.214,12.941,12.992,13.372,13.527 --tl 0`.
