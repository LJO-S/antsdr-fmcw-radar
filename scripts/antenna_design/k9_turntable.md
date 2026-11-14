# K9 bench: solder, S-parameters, turntable (LiteVNA)

Guide 9.5, done on the VNA instead of the E200. In the radar, the E200's own TX1 -> RX1
leak sits in the same range cell as the turntable path, only ~5-10 dB under it, and bends
the A-B phase by tens of deg (`TODO.md`, K9). The VNA has no such leak. It measures the
curve's shape; the radar later adds one constant, psi0 (`TODO.md`, K11).

Out: the batch's eps_r (sets fc), S11 per column, A-B coupling, TX-RX isolation, the TX
board's azimuth pattern (A and B), the column gain, the beam tilt, and the A-B phase vs
angle that K10 uses as its lookup.

## Checklist

Prep, indoors:

- [x] 0 - solder, witness, column S11, coupling (2026-11-09, `results.md` K9).
- [ ] Print (`mount/make_stl.sh` -> `local/mount/`): `bracket_tx`, `bracket_rx`, `mast_head`,
      `mast_base`, `pole_top`, `clamp_35` (~100 g PLA; radar adds 3x `clamp_20`). Hardware, each with a nut:

      - M2.5x12 x8, 
      - M2.5x20 x1, 
      - M2.5x8 x4 (switch), 
      -M2x12-16 x8 (boards); 
      - no socket screws: tape the pipe / pole ends snug, tape round each joint; 
      - gaffer or double-sided foam tape (lazy susan to `pole_top` and `mast_base`). 
      - 20 mm PVC pipe ~200 mm (mast).
- [ ] Boards on the brackets, feed end at `< FEED`.
- [ ] Nano: flash `k9_switch` (1.1), wire it, bench test: `a` / `b` flips S21 between A and B.
- [ ] Links labelled: A (J1) -> RF2, B (J2) -> RF1 (1.2).
- [ ] Turntable: `pole_top` on the TX tripod, lazy susan, `mast_base`, mast, `mast_head`,
      `bracket_tx` upright, feed end up (1.3). Angle scale on the rim (1.4).
- [ ] Pack the Kit list.

Field, calm dry day (~3 h): 2 set up -> 3 calibrate -> 4 zero + switch check -> 5 azimuth
sweep (39 angles x A, B) -> 6 gain -> 7 isolation -> 8 elevation cut. Home: 9 analyse.

## Kit

- Section 0: the K5 soldering kit, calipers, 5 Molex SMAs (TX 3, RX 2), the K8 line-pair
  `.s2p` files (`~/work/projects/sdr/nanovna/patch_witness/`).

- LiteVNA-64 + cal kit; laptop with NanoVNA-Saver >= 0.7.0 (USB).
- Port 1 cable: both 2 m HF240s joined by an f-f barrel (4 m). Port 2 cable: the 0.3 m VNA
  cable. A second f-f barrel for the calibration.
- TX board, RX board, the switch module that will go on the radar, and the two m-m links
  (adapters or jumpers) from its RF1 / RF2 to the board. They are part of the calibration:
  never swap or replace them afterwards.
- 3.3 V bench supply (current limit 100 mA), an Arduino, Dupont leads that reach the board
  (~2 m), and a 1 k + a 2 k resistor if the Arduino is a 5 V board.
- Two tripods (35 mm poles), the lazy susan, the printed mount (`mount/k9_mount.scad`) with
  its bolts, the 20 mm pipe mast, the printed angle scale (1.4), tape,
  tape measure, spirit level, a laser pointer and a small mirror.
- Mains at the site for the supply.

## Rules

- **The VNA radiates.** Outdoors, sweep 5.725-5.875 GHz only (SRD band). Even 0 dBm out is
  ~+3 dBm EIRP, far under the 14 dBm cap. Section 0's wide sweep is indoors and brief, as K8.
- **ESD** (switch class 1A): touch ground before touching the module, a lead or an antenna.
- **Power order**: supply on, then the Arduino; off in reverse. V_CTL must never exceed
  VDD + 1 V.
- **A = RF2 = V_CTL low** (the radar's `tx_sel` 0), **B = RF1 = V_CTL high**.
  **+theta = the RX board on B's side** of the TX board's normal (as `FakeTarget.theta0`).
- Between the A and the B reading of one angle, touch nothing.

## 0. Solder and check (indoors) [DONE]

Sweep 5.0-6.3 GHz, 401 points, as K8 (`k8_analyze.py` needs K8's grid). Files into
`scripts/antenna_design/local/k9_bench/<date>/`.

1. **Before the iron**: calipers on each board at the tab and the column's far end (K8
   cores 1.49 / 1.54 mm: tests the two-panel fit, `results.md`). Mask open over the
   copper, SMA ground holes plated.
2. **Witness SMAs, both boards, first.** Solder as on K8: same solder amount, centre pin
   flat on the trace. The eps_r readout removes K8's launch, so the launch must match.

   ```
   python k8_analyze.py --patch <witness>.s1p --short <K8 short>.s2p --long <K8 long>.s2p
   ```

   Raw dip near K8's 5.819 GHz / -11.5 dB = same launch. Launch-removed eps_r -> fc
   (5.750-5.850 GHz, guide 9.4). Both boards should agree. Keep the tab until this is done.
3. **RX column, then TX A and B.** S11 of each right after soldering. The launch dominates
   (-6 to -8 dB): judge the resonance and whether A matches B, not the depth.
4. **Coupling**: S21 between A and B (port 1 on A, port 2 on B). Target <= -20 dB, sim -24.

## 1. Prepare (indoors)

1. **Switch control.** Toggle V_CTL, not EN: EN goes to GND and stays there (EN high
   switches both ports off).

   | Module pin | To |
   |---|---|
   | VDD | supply +3.3 V |
   | GND | supply minus and Arduino GND |
   | EN | GND |
   | V_CTL | 3.3 V Arduino: a digital pin. 5 V Arduino: pin -> 1 k -> V_CTL, V_CTL -> 2 k -> GND (3.3 V high) |

   Sketch: `k9_switch/k9_switch.ino` (Nano, D2; serial 115200, `a` / `b` / `?`):

   ```
   arduino-cli core install arduino:avr
   arduino-cli compile --fqbn arduino:avr:nano scripts/antenna_design/k9_switch
   arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:nano:cpu=atmega328old scripts/antenna_design/k9_switch
   arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=115200
   ```

   `atmega328old` is the clone bootloader; if upload fails, drop `:cpu=...`. Port access
   like NanoVNA-Saver: `sg dialout -c "..."`.
2. **Links**: column A (J1, outer) -> switch RF2, column B (J2, next to the witness) -> RF1.
   Label both columns and both links.
3. **Mast**: mark the lazy susan's centre (two diameters). `mast_base` with its centre hole
   over the mark, arrow towards the RX tripod; tape it down. The mount puts
   the patch face in a plane through the centre and the centre line between the columns
   over it (spec: within 1 cm; 5 cm off gives 0.6 deg at 30 deg). Columns vertical (spirit
   level). Nylon bolts near the board, no metal within 10 cm. **Feed end up**: the switch
   sits on the bracket's back near the feed end and would hit the pipe below the head. The
   two Wurth links loop from the switch to J1 / J2; tape the loops so they never move.
   Leads and the port 1 cable down the back of the mast.
4. **Angle scale**: print `local/mount/protractor.pdf` at 100 % (`mount/protractor.py`,
   `--d` = rim diameter, default 295 mm); check the 100 mm bar. Cut out set 1, join the two
   pieces at the overlap, wrap it round the top's rim, ticks down. Pointer: a strip of card
   taped to the fixed base, bent up to the rim. Tape the base to `pole_top` so it cannot turn.

## 2. Set up (outdoors)

1. Calm day, open ground, no walls or cars within ~10 m. Column centres at the same
   height, >= 1.5 m. Patch faces 2.5 m apart (far field 2.05 m). RX board vertical, feed
   end the same way as the TX board, aimed at the turntable by eye (it never moves, so its
   aim is common to every angle).
2. VNA on the RX tripod, below the RX board. Port 2 -> 0.3 m cable -> RX column. Port 1 ->
   4 m cable -> switch RFC, hanging at least 0.5 m below the line between the boards, with
   a ~30 cm loose loop at the mast so a 90 deg turn does not pull it. If it does not reach
   without crossing that line, get a longer cable rather than raise it into the beam.
3. Supply and Arduino on the ground behind the RX tripod, leads to the module. Supply on,
   then the Arduino (USB to the laptop).

## 3. Calibrate

1. **Settings**: 5.725-5.875 GHz, 201 points, IFBW 100 Hz (or the nearest), default output
   power. NanoVNA-Saver sweeps exactly this span.
2. **Cal** (as K7), port 1 cable looped back to the VNA: barrel on the port 1 cable end;
   SOL on the barrel face; isolation with loads on the barrel and on the port 2 cable;
   thru = port 2 cable into the barrel. Remove the barrel (S21 then reads ~0.1 dB high).
   Save to a memory slot.
3. **Check**: thru again, 0 dB and 0 deg flat. Loads on both ends: S21 <= -75 dB, else
   lower the IFBW and recalibrate.
4. **Connect**: port 1 cable -> switch RFC, port 2 cable -> RX column. Laying the 4 m cable
   out moves it by ~0.1 dB and a phase common to A and B: harmless. From here only the
   turntable moves the cables.

## 4. Zero and switch check

1. **Zero**: tape the mirror flat on the TX board's front, centred. Hold the laser at the
   RX column's centre height, just in front of it, aimed at the mirror. Turn the lazy susan
   until the spot lands back on the laser (1 deg off moves it ~9 cm). Re-tape the pointer
   (not the board) to read 0. Remove the mirror.
2. **Switch check**: at 0, read (do not save) the S21 phase at fc after `a`, then after
   `b`. Turn to +30 and do it again. `(angle B - angle A)` at +30, minus the same at 0,
   should be about +96 deg (sim).
   - About -96: + and - or A and B are swapped. Fix the labels now (+ and -: scale set 2).
   - No change between `a` and `b`: the switch is not switching (VDD, EN, V_CTL).

## 5. Sweep

Order: 0, +5, ..., +90, back to 0, -5, ..., -90, back to 0. At each angle:

1. Turn, let the board stop moving, walk back to the same spot behind the RX tripod.
2. `a`, sweep, save `<angle>_A.s2p`; `b`, sweep, save `<angle>_B.s2p`. Angles as `p030`,
   `m030`; the three zeros `z1`, `z2`, `z3`.
3. Into `scripts/antenna_design/local/k9_turntable/<date>/` (git-ignored).

**Drift**: at `z2` and `z3`, `(angle B - angle A)` within 1 deg of `z1` and the levels
within 0.2 dB. Else something moved (cable, board on the mast): find it, redo that half.

## 6. Gain

At 0: R = patch face to patch face (tape measure). Per column, at fc:

```
G = 0.5 * (S21_dB + 3.0 + FSPL)     # dBi: mean of the TX column (in the pair) and the RX column
FSPL = 20*log10(4*pi*R*fc / c)       # 55.7 dB at 2.5 m
```

3.0 dB is the switch (K7); add the link's loss. Expect ~10.5 dBi (sim 10.3 TX, 10.8 RX).
A beam tilted up reads low here (both columns, ~1.7 dB each at 5 deg): section 8 separates
the two.

## 7. Isolation

Turntable at 0, state `a`. RX board beside the TX board, faces in one plane, as on the
radar. S21 at 3-4 spacings (edge gap 5 to 50 cm), saved as `iso_<cm>.s2p`. Reference: the
E200 leak with the COTS antennas at 1.5 m (guide 9.5).

## 8. Elevation cut (beam tilt)

The K9 batch is eps_r ~4.8, not the design's 4.6: expect the beam ~+4-5 deg off broadside
at 5.8 GHz (sim slope ~2.8 deg per 100 MHz). This measures it. Last, since it re-mounts both
boards.

1. Both boards on their side (columns horizontal, co-pol kept): unbolt each bracket, turn it
   90 deg, bolt it back (TX on `mast_head`, RX on `clamp_35`). The column's middle stays over
   the centre. Note which side the feed end points to.
2. Zero with the mirror (section 4). State `a` only.
3. -20 to +20 deg in 2 deg steps (mark them), `el_p04.s2p`, `el_m04.s2p`, ...

## 9. Analyse (your script)

At fc (5.750-5.850 GHz):

- **Pattern**: |S21_A| and |S21_B| vs angle, dB re their peak. HPBW (sim ~90 deg, embedded),
  first sidelobes; A and B should be mirror images. Ripple = multipath (guide 12).
- **Curve**: `psi(theta) = -(angle(S21_B) - angle(S21_A))` (the radar's sign: conj(RX)),
  unwrapped over angle. `c(theta) = psi(theta) - psi(0)`. Sim: ~-96 deg at +30, 4.6 deg
  rms off `-2*pi*d*sin(theta)/lambda` (d 25.9 mm) over +-60.
- **Zero check**: `c(theta) + c(-theta)` ~ 0. A steady sign means the zero is off: fit the
  shift that makes `c` odd.
- **Tilt**: elevation |S21| at 5.75 / 5.80 / 5.85 GHz, parabola through the top +-6 deg (dB)
  -> peak angle, towards or away from the feed end. Tilt vs f extrapolated to 0 = the
  broadside frequency. Mount the boards tilted by minus the tilt at the chosen fc.
- **Write down** in `results.md`, K9 "Measured": thickness, witness eps_r, fc, S11 per
  column, coupling, isolation vs spacing, R, gain, tilt vs f, HPBW, sidelobes, and `c` at
  5 deg steps over +-60.

The radar then reads `theta = c^-1(psi - psi0)`, with psi0 from the field (`TODO.md`, K11).
