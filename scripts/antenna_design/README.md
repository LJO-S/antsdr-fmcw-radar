# Antenna design (K8, guide 9.3)

openEMS FDTD simulations of the 5.8 GHz inset-fed patch on FR4.

| File | What |
|---|---|
| `Simple_Patch_Antenna.py` | One patch: S11 at the board edge, Zin at the feed point, pattern, efficiency, gain, tuning hints, KiCad summary |
| `sweep_eps_r.py` | The eps_r 4.4 geometry on eps_r 4.2-4.6: the calibration curve, `--measured <GHz>` -> eps_r |
| `results.md` | K8 and K9 designs, sims and measurements (hand-kept) |
| `eps_r_sweep.md`, `.png` | Sweep table and plot (generated) |
| `sweep/` | Per-run `.npz` the sweep reads, plus plots; delete an `.npz` to re-simulate it |
| `antenna-bringup/` | KiCad: the patch board (= the simulated board) |
| `line-pair/` | KiCad: two 50 Ohm lines, 95 and 25 mm, for eps_eff and loss |
| `line_pair_sim.py` | The line pair in openEMS: predicted phase, eps_eff, lambda_g, loss vs eps_r |
| `line_pair_sim/` | Its per-run `.npz` / `.s2p` (local, git-ignored; `--all` regenerates) |
| `k8_analyze.py` | VNA Touchstone files -> patch eps_r (launch removed via the line pair); line pair eps_eff, lambda_g, dB/mm, tan d, eps_r |
| `test_k8_analyze.py` | Its board-free tests (`python test_k8_analyze.py`) |
| `column_sim.py` | K9: series-fed 1xN column(s), one port per column, S11/S21, pattern cuts, A-B phase, KiCad summary |
| `column/` | Its per-run data (local, git-ignored) |
| `sma_launch_sim.py`, `sma_launch/` | The Molex edge SMA in openEMS: its bias on the K8 readings (data local) |
| `make_footprints.py` | Simulated copper -> KiCad 10 footprints in `antenna.pretty/` (K9 column, K8 witness patch); `test_make_footprints.py` |

## Run

```bash
source ~/opt/openEMS/venv/bin/activate     # deactivate the radar .venv first
python Simple_Patch_Antenna.py [--design 4.4] [--eps-r E] [--out DIR] [--refine R]
python sweep_eps_r.py [--measured GHz]
python line_pair_sim.py --all              # simulate what is missing (the table k8_analyze reads)
python k8_analyze.py --patch p.s1p --short s.s2p --long l.s2p [--out DIR]   # any subset
```

- `k8_analyze.py` reads both tables (`sweep/`, `line_pair_sim/`) and runs the simulated
  lines through the same code as the measurement. Line loss and eps_eff come from the
  eigenvalues of `T_long inv(T_short)`, which cancels identical launches exactly; the
  plain S21 difference is printed as a cross-check. The LiteVNA measures no S22: it
  assumes S22 = S11.

- Runs are tagged `d<design eps_r>_e<substrate eps_r>_r<refine>`: field data in
  `/tmp/Patch_Inset_<tag>`, saved results in `<out>/<tag>.*`.
- `--refine` scales the mesh (xy and substrate cells together). Converged (2026-10-05):
  the S11 null sits at 5.789 / 5.801 / 5.804 GHz at refine 1 / 1.5 / 2 (1.8 / 5.5 / 13 M
  cells, ~45 s / ~3.5 min / ~10 min). The sweep uses 1.5; 1 is fine for tuning, ~15 MHz low.
- x is the resonant direction (the feed runs along x): the reverse of Balanis' L/W naming.
- The copper is PEC with zero thickness and no solder mask: open the mask over the patch.

## Boards (KiCad 10, 2026-10-05)

Two designs, one JLCPCB order: FR-4 TG135-140, 1.6 mm, 1 oz, HASL, order number at the
`JLCJLCJLCJLC` text (bottom). Gerbers in `*/gerbers/`, read back and checked against `results.md`.

- **SMA**: Molex 73251-1153 (18 GHz, 1.60 mm board), stock footprint
  `SMA_Molex_73251-1153_EdgeMount_Horizontal`. Checked against `docs/datasheets/SD-73251-115.PDF`:
  widths and pitch match; pads 5.08 mm vs 5.84 drawn, holes 0.75 mm nearer the edge. Kept (the
  4.75 mm legs still land). Board edge = pad start = footprint x -4.26.
- **Launch**: the line starts at the end of the 2.29 mm centre pad (x 5.0), not over it. The
  narrow pad offsets the pin's capacitance (Molex's launch). Not in the sim: the S11 null depth
  will differ; the resonance and the line pair's phase difference do not care.
- **Patch board**: the `results.md` geometry as filled F.Cu rectangles (stroke 0), whole top
  mask open, no top silk plotted, solid GND plane on B.Cu.
- **Line pair**: 2.868 mm lines (the feed width), edge to edge 95 and 25 mm, so dL = 70 mm.
  Phase difference -> eps_eff with the launches cancelled and no antenna model: a check on
  the sim, and K9's element pitch (lambda_g). Loss difference -> tan d (~1.2 of ~1.4 dB is
  dielectric). Z0 ~50-56 Ohm expected, irrelevant to dphi; measure it with TDR.
- **KiCad habits**: grid origin at the board edge on the line centre, display origin = grid
  origin, so dialogs read `results.md` numbers. Whole-top mask opening -> mask-bridge rule
  ignored. Pads at the edge: excluded (patch) or the `line-pair.kicad_dru` rule (min -0.01 mm:
  touching counts as a collision at 0). After every zone fill, view that layer alone: DRC
  passes a no-net or thermal-relieved plane.

## openEMS install

Built from source: the apt package is a 2019 snapshot (never take `sudo apt install
openems`). Installs to `~/opt/openEMS`, with its own Python venv.

```bash
sudo apt-get install build-essential cmake git libhdf5-dev libvtk9-dev \
    libboost-all-dev libcgal-dev libtinyxml-dev qtbase5-dev libvtk9-qt-dev \
    python3-pip python3-venv
git clone --recursive https://github.com/thliebig/openEMS-Project.git
cd openEMS-Project && ./update_openEMS.sh ~/opt/openEMS --python --njobs=4
echo 'export PATH="$HOME/opt/openEMS/bin:$PATH"' >> ~/.bashrc
```

- `Killed` or a compiler internal error = out of RAM: rerun with `--njobs=2`.
- The dependency check may list `python3-numpy`, `cython3`, `python3-h5py`, ... as missing:
  harmless with `--python`, the venv has its own copies.
- Check: `openEMS` prints a banner, `AppCSXCAD` opens an empty viewer,
  `python3 -c "import CSXCAD, openEMS"` works inside the venv.

## K9 layout (`make_footprints.py`)

- `python make_footprints.py [column_sim options]` writes `antenna.pretty/`: the column and
  the witness patch, each one custom pad "1" (F.Cu + F.Mask). Same options as
  `column_sim.py`, so a re-tune or a taper is one command. Origin = board edge on the line
  centre. The first 5 mm of line is left to the SMA centre pad, as on K8 (`--launch-gap`).
- The board to fabricate is the eps_r 4.6 re-tune (`results.md`, K9): pass its options, as
  the defaults are still the 4.4 baseline.
- TX board: two columns at y = +-12.95 mm, Edge.Cuts 225.99 x 71.63 mm (y +-35.81). RX
  board: one column, 225.99 x 45.73 mm (y +-22.86). Solid GND on B.Cu, as simulated.
- The SMA footprint's two plated ground holes are the launch sim's vias: the drill file
  must be in the order. The K8 launch measured like one without them (`results.md`, K8).
- Step-by-step KiCad -> Gerbers: `TODO.md`, K9.
- Witness patch: the K8 table assumes the K8 board. Keep its ground margins (about
  20 mm on every side) and compare it with the K8 measurement, not the table alone.

## K8 measurement (LiteVNA 64)

Predictions first (`results.md`): patch S11 minimum near 5.8 GHz for eps_r 4.4 (about 60 MHz
per 0.1 eps_r, launch shifts it 20-35 MHz); line pair eps_eff 3.49, lambda_g 27.7 mm, dphi
~911 deg over dL 70 mm, ~0.020-0.021 dB/mm (all at eps_r 4.4; 4.2-4.6 spans eps_eff 3.34-3.64).

1. **Settings**: 5.0-6.3 GHz (the LiteVNA-64's top), 401 points or more, the same sweep for
   both boards (the launch removal needs it), highest averaging / lowest IF bandwidth the
   menu offers, default output power.
2. **Calibrate** SOL on port 1 + thru + isolation (the LiteVNA is 1.5-port: port 2 gets no
   SOL), with the same span and points as the measurement. Changing span or points means
   recalibrating; NanoVNA-Saver must sweep exactly the calibrated span. Save it to a memory slot.
3. **Check**: re-measure the cal load and the barrel thru. Both should look near perfect
   (they were the standards); if not, recalibrate. Fix the cables for the whole session.
4. **Save**: use NanoVNA-Saver 0.7.0 or newer (first LiteVNA support) with the USB link, then
   File -> save .s1p / .s2p (Touchstone v1, as `k8_analyze.py` expects). On-device saving
   to microSD: format unconfirmed, check it is .s1p/.s2p with S11/S21 in the file.
5. **Patch**: screw the board straight onto the cable end, board held in free air by the
   cable or a stand (cable taped in place), face pointing away from you, hand and metal
   objects more than 20 cm away, nothing behind it. Same pose as the calibration cable
   layout. Save `patch.s1p`.
6. **Line pair**: same calibration, same cables, do not move or re-flex them between boards
   (only unscrew the DUT). Port 1 -> port 2 across each line, laid flat on foam or
   cardboard, not on a metal bench. Save `short.s2p` (25 mm) and `long.s2p` (95 mm), S11 and S21.
   Optional: flip each line (ports swapped), save `short_flip.s2p` / `long_flip.s2p`; flipped
   S11 should match the S22 the analysis assumes (equal to S11).
7. **Analyse**: `python k8_analyze.py --patch patch.s1p --short short.s2p --long long.s2p --out k8/`
   (`--dl`, `--width`, `--h`, `--l-short` default to the design 70, 2.868, 1.5, 25). Copy from
   `k8/k8_summary.md` into `results.md`: eps_r from the patch and from the lines (table and
   cross-check), eps_eff, lambda_g, dB/mm, tan d range. Done (guide 9.3): the two eps_r agree
   within ~0.1.
   With all three files, the line pair also yields the launch (eigenvectors of the T-matrix
   ratio, plus the short line's S11 with mirrored launches) and removes it from the patch S11.
   That eps_r is the patch value: the real launch moved the dip +140 MHz on the first boards,
   not the simulated 25 MHz. It assumes both boards have the same launch; a max |S11| above
   0 dB after removal (off resonance) shows how far that holds.

Sources: [NanoVNA-Saver v0.7.0 release](https://github.com/NanoVNA-Saver/nanovna-saver/releases)
(LiteVNA support), [Selteq LiteVNA 64 specs](https://selteq.com.ua/en/litevna-64/) (points),
[Oscar Liang](https://oscarliang.com/?p=45815) (calibration flow, recalibrate on span change).
