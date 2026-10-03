# openEMS simulations

## Phase 6 K8
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