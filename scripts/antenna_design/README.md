# Antenna design (K8, guide 9.3)

openEMS FDTD simulations of the 5.8 GHz inset-fed patch on FR4.

| File | What |
|---|---|
| `Simple_Patch_Antenna.py` | One patch: S11 at the board edge, Zin at the feed point, pattern, efficiency, gain, tuning hints, KiCad summary |
| `sweep_eps_r.py` | The eps_r 4.4 geometry on eps_r 4.2-4.6: the calibration curve, `--measured <GHz>` -> eps_r |
| `results.md` | Designs per eps_r and the board to fabricate (hand-kept) |
| `eps_r_sweep.md`, `.png` | Sweep table and plot (generated) |
| `sweep/` | Per-run `.npz` the sweep reads, plus plots; delete an `.npz` to re-simulate it |
| `antenna-bringup/` | KiCad: the patch board (= the simulated board) |
| `line-pair/` | KiCad: two 50 Ohm lines, 95 and 25 mm, for eps_eff and loss |

## Run

```bash
source ~/opt/openEMS/venv/bin/activate     # deactivate the radar .venv first
python Simple_Patch_Antenna.py [--design 4.4] [--eps-r E] [--out DIR] [--refine R]
python sweep_eps_r.py [--measured GHz]
```

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

- **SMA**: Molex 73251-1150 (18 GHz, 1.60 mm board), stock footprint
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
