# -*- coding: utf-8 -*-
"""
Microstrip line pair, 5.8 GHz on FR4 (Part K8, guide 9.3)

The two 50 Ohm lines of line-pair/line-pair.kicad_pcb (95 and 25 mm edge to edge, 2.868 mm
wide, solid ground on B.Cu), one line per run, to predict what the VNA will measure: the S21
phase difference gives eps_eff (the launches cancel), the loss difference gives dB/mm.
Same substrate, kappa (tan d), mesh rules and MSL ports as Simple_Patch_Antenna.py.

    python line_pair_sim.py --length 95 [--eps-r 4.4] [--copper] [--refine 1] [--out DIR]
    python line_pair_sim.py --all     # every case below, then line_pair_sim.md

Based on the openEMS "Simple Patch Antenna" tutorial.
"""

### Import Libraries
import argparse, os, subprocess, sys, tempfile, time
import numpy as np
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "line_pair_sim")
LENGTHS = (95.0, 25.0)  # mm, edge to edge (KiCad board)
F_MIN, F_MAX = 5.0e9, 6.5e9  # the VNA span
BAND = (5.75e9, 5.85e9)
C0_ = 299792458.0
LINE_W = 2.868  # mm, the feed width of the patch board


def make_tag(length, eps_r, copper, refine, thick=False):
    return "L{:g}_e{:.2f}_{}_r{:g}{}".format(
        length, eps_r, "cu" if copper else "pec", refine, "_t35" if thick else ""
    )


def unwrap_phase(s21):
    """Unwrapped S21 phase (rad) with the integer wrap fixed physically: the reference
    planes are the board edges, so the phase must extrapolate to ~0 at f = 0 (a pure delay).
    A straight-line fit over the band gives the intercept; remove the nearest 2 pi multiple.
    """
    ph = np.unwrap(np.angle(s21))
    f = np.linspace(F_MIN, F_MAX, len(s21))
    icpt = np.polyfit(f, ph, 1)[1]
    return ph - 2 * np.pi * np.round(icpt / (2 * np.pi))


### Driver: --all simulates what is missing, then writes line_pair_sim.md
def hammerstad_jensen(er, w, h):
    """Quasi-static eps_eff of a microstrip (Hammerstad-Jensen 1980, with the Hammerstad-Bekkadal a(u), b(eps_r) exponent)."""
    u = w / h
    a = (
        1
        + np.log((u**4 + (u / 52) ** 2) / (u**4 + 0.432)) / 49
        + np.log(1 + (u / 18.1) ** 3) / 18.7
    )
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def sweep_all():
    # (eps_r, copper, refine, thick): PEC at three eps_r at refine 1.5, a 35 um thick strip,
    # copper sheet loss and the refine 1 reference for it (also the convergence check)
    cases = [
        (4.2, False, 1.5, False),
        (4.4, False, 1.5, False),
        (4.6, False, 1.5, False),
        (4.4, False, 1.5, True),
        (4.4, False, 1, False),
        (4.4, True, 1, False),
    ]
    os.makedirs(OUT, exist_ok=True)
    for eps, cu, ref, th in cases:
        for length in LENGTHS:
            tag = make_tag(length, eps, cu, ref, th)
            if os.path.exists(os.path.join(OUT, tag + ".npz")):
                continue
            cmd = [
                sys.executable,
                "-u",
                os.path.abspath(__file__),
                "--length",
                str(length),
                "--eps-r",
                str(eps),
                "--refine",
                str(ref),
                "--out",
                OUT,
            ]
            if cu:
                cmd.append("--copper")
            if th:
                cmd.append("--thick")
            if os.environ.get(
                "OPENEMS_LOCK"
            ):  # one simulation at a time on a shared machine
                cmd = ["flock", os.environ["OPENEMS_LOCK"]] + cmd
            print(
                "simulating {} (log: line_pair_sim/{}.log)".format(tag, tag), flush=True
            )
            t0 = time.time()
            with open(os.path.join(OUT, tag + ".log"), "w") as log:
                subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
            print("  done in {:.0f} s".format(time.time() - t0), flush=True)

    def analyse(eps, cu, ref, th):
        """Per-case numbers from the pair of saved runs."""
        d = {
            L: np.load(os.path.join(OUT, make_tag(L, eps, cu, ref, th) + ".npz"))
            for L in LENGTHS
        }
        f = d[95.0]["f"]
        i = int(np.argmin(np.abs(f - 5.8e9)))
        dL = LENGTHS[0] - LENGTHS[1]
        # phase difference as a positive lag of the long line
        dphi = unwrap_phase(d[25.0]["s21"]) - unwrap_phase(d[95.0]["s21"])
        eps_eff = (C0_ * dphi[i] / (2 * np.pi * f[i] * dL * 1e-3)) ** 2
        loss = (
            20 * np.log10(np.abs(d[25.0]["s21"][i]))
            - 20 * np.log10(np.abs(d[95.0]["s21"][i]))
        ) / dL
        band = (f >= BAND[0]) & (f <= BAND[1])
        s11w = {L: 20 * np.log10(np.max(np.abs(d[L]["s11"][band]))) for L in LENGTHS}
        return dict(
            dphi=np.degrees(dphi[i]),
            eps_eff=eps_eff,
            lam_g=C0_ / (5.8e9 * np.sqrt(eps_eff)) * 1e3,
            loss=loss,
            s11w=s11w,
            z0=d[95.0]["z_ref"][i],
        )

    res = {c: analyse(*c) for c in cases}
    pec = [res[(e, False, 1.5, False)]["eps_eff"] for e in (4.2, 4.4, 4.6)]
    slope = np.polyfit([4.2, 4.4, 4.6], pec, 1)[0]
    h = float(np.load(os.path.join(OUT, make_tag(95.0, 4.4, False, 1.5) + ".npz"))["h"])
    base, thick = res[(4.4, False, 1.5, False)], res[(4.4, False, 1.5, True)]
    r1, cu_r = res[(4.4, False, 1, False)], res[(4.4, True, 1, False)]
    closs = cu_r["loss"] - r1["loss"]

    def row(c, label):
        r = res[c]
        return "| {:.1f} | {} | {:g} | {:.1f} | {:.3f} | {:.2f} | {:.4f} | {:.1f} / {:.1f} | {:.1f} |".format(
            c[0],
            label,
            c[2],
            r["dphi"],
            r["eps_eff"],
            r["lam_g"],
            r["loss"],
            r["s11w"][95.0],
            r["s11w"][25.0],
            r["z0"].real,
        )

    head = [
        "| eps_r | copper | refine | dphi (deg) | eps_eff | lambda_g (mm) | loss (dB/mm) | "
        "worst S11 95 / 25 mm (dB) | Z0 (Ohm) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    md = [
        "<!-- Generated by line_pair_sim.py --all. Do not edit by hand. -->",
        "",
        "# Line pair: simulated VNA prediction (K8)",
        "",
        "Lines 95 and 25 mm edge to edge (dL = 70 mm), {:.3f} mm wide, h {:.1f} mm, tan d 0.02, "
        "reference planes at the board edges. Phase difference at 5.8 GHz, unwrapped; "
        "loss = (|S21| 25 mm - |S21| 95 mm) / dL at 5.8 GHz. PEC = dielectric loss only.".format(
            LINE_W, h
        ),
        "",
        "Main table, refine 1.5 (same mesh rule as the patch table):",
        "",
    ] + head
    md += [row((e, False, 1.5, False), "PEC") for e in (4.2, 4.4, 4.6)]
    md += [
        row((4.4, False, 1.5, True), "PEC, 35 um thick"),
        "",
        "Checks at refine 1:",
        "",
    ] + head
    md += [
        row((4.4, False, 1, False), "PEC"),
        row((4.4, True, 1, False), "35 um sheet"),
    ]
    md += [
        "",
        "- Slope (PEC, refine 1.5): d(eps_eff)/d(eps_r) = {:.3f}, so eps_r = 4.4 + (eps_eff_meas - {:.3f}) / {:.3f}.".format(
            slope, base["eps_eff"], slope
        ),
        "- Thickness: a 35 um thick PEC strip gives eps_eff {:.3f} vs {:.3f} for zero thickness ({:+.4f}), "
        "dphi {:+.1f} deg (eps_r 4.4, refine 1.5).".format(
            thick["eps_eff"],
            base["eps_eff"],
            thick["eps_eff"] - base["eps_eff"],
            thick["dphi"] - base["dphi"],
        ),
        "- Convergence, eps_eff at eps_r 4.4, refine 1 -> 1.5: {:.3f} -> {:.3f} ({:+.4f}).".format(
            r1["eps_eff"], base["eps_eff"], base["eps_eff"] - r1["eps_eff"]
        ),
        "- Quasi-static Hammerstad-Jensen eps_eff at eps_r 4.2 / 4.4 / 4.6: {}.".format(
            " / ".join(
                "{:.3f}".format(hammerstad_jensen(e, LINE_W, h))
                for e in (4.2, 4.4, 4.6)
            )
        ),
        "- Conductor loss, sim copper sheet minus PEC (smooth copper, refine 1): {:.4f} dB/mm = {:.2f} dB "
        "over the 70 mm difference; dielectric {:.4f} dB/mm. Real 1 oz HASL FR4 is an estimate of "
        "1.1-2.0 dB/m (roughness factor 1.7-1.9, HASL +10-30 %), not simulated.".format(
            closs, 70 * closs, r1["loss"]
        ),
        "- Z0 is the line impedance the port extracted (real part, 5.8 GHz).",
    ]
    with open(os.path.join(HERE, "line_pair_sim.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("\n".join(md))


### General parameter setup
parser = argparse.ArgumentParser(description="50 Ohm microstrip line on FR4 (openEMS)")
parser.add_argument(
    "--length", type=float, choices=LENGTHS, help="line length in mm, edge to edge"
)
parser.add_argument(
    "--eps-r", type=float, default=4.4, help="eps_r of the substrate (default 4.4)"
)
parser.add_argument(
    "--copper",
    action="store_true",
    help="35 um copper sheet (5.8e7 S/m) instead of PEC, for conductor loss",
)
parser.add_argument(
    "--thick",
    action="store_true",
    help="strip as a 35 um thick PEC box (ground stays a sheet); fine z mesh, slow",
)
parser.add_argument("--out", help="save plots and data here instead of showing them")
parser.add_argument(
    "--refine",
    type=float,
    default=1.0,
    help="mesh refinement factor, xy and substrate cells together (convergence check)",
)
parser.add_argument(
    "--all", action="store_true", help="run every case, then write line_pair_sim.md"
)
args = parser.parse_args()
if args.all:
    sweep_all()
    sys.exit(0)
if args.length is None:
    parser.error("--length is required without --all")
if args.out:
    args.out = os.path.abspath(args.out)  # FDTD.Run chdirs into Sim_Path
    plt.switch_backend("Agg")

from CSXCAD import ContinuousStructure
from openEMS import openEMS
from openEMS.physical_constants import *

substrate_epsR = args.eps_r
tag = make_tag(args.length, substrate_epsR, args.copper, args.refine, args.thick)
Sim_Path = os.path.join(tempfile.gettempdir(), "LinePair_" + tag)

f0 = 5.75e9  # centre of the VNA span
fc = (
    1.5e9  # 20 dB corner: the pulse covers 4.25-7.25 GHz, so 5.0-6.5 GHz is well inside
)
unit = 1e-3  # all dimensions in mm

# substrate setup, same tan d definition as the patch script
substrate_kappa = (
    0.02 * 2 * np.pi * 5.8e9 * EPS0 * substrate_epsR
)  # tan(delta) = 0.02 at 5.8 GHz
substrate_thickness = (
    1.5  # h: dielectric only. JLCPCB 2-layer "1.6 mm" = 1.5 mm core + copper
)
substrate_cells = round(4 * args.refine)

# Board as in KiCad: the line is 15 mm of board wide (strip centred), x from 0 (edge) to length
feed_R = 50
line_width = LINE_W
length = args.length
board_w = 15.0
port_len = 8.0  # MSL port length; probes sit halfway, then the reference plane is moved back to the edge

# Air around the board: 10 mm beyond each edge in x, 15 mm sideways, 25 mm above
Box = dict(
    x=(-10, length + 10),
    y=(-board_w / 2 - 15, board_w / 2 + 15),
    z=(-15, substrate_thickness + 25),
)

print(
    "line {:g} mm x {:.3f} mm on {:.0f} mm board, eps_r {:.2f}, {}".format(
        length,
        line_width,
        board_w,
        substrate_epsR,
        "copper 35 um" if args.copper else "thick PEC" if args.thick else "PEC",
    )
)

### FDTD setup
# the 17 um cells of --thick cut the timestep ~30x, so it needs far more steps
FDTD = openEMS(NrTS=600000 if args.thick else 60000, EndCriteria=1e-4)
FDTD.SetGaussExcite(f0, fc)
FDTD.SetBoundaryCond(["MUR", "MUR", "MUR", "MUR", "MUR", "MUR"])

CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(unit)
# lambda/20 inside the substrate at the highest simulated frequency (same rule as the patch)
mesh_res = C0 / (f0 + fc) / unit / np.sqrt(4.4) / 20 / args.refine
edge_res = mesh_res / 2


def add_metal_edge(direction, pos, metal_side):
    """Thirds rule: one mesh line 1/3 inside the metal edge, one 2/3 outside (metal_side +-1)."""
    mesh.AddLine(
        direction,
        [pos + metal_side * edge_res / 3, pos - metal_side * 2 * edge_res / 3],
    )


### Generate properties, primitives and mesh-grid
mesh.AddLine("x", [Box["x"][0], Box["x"][1], 0, length, port_len, length - port_len])
mesh.AddLine("y", [Box["y"][0], Box["y"][1], -board_w / 2, board_w / 2])
mesh.AddLine("z", [Box["z"][0], Box["z"][1]])
for s in (+1, -1):
    add_metal_edge("y", s * line_width / 2, -s)
mesh.AddLine("z", np.linspace(0, substrate_thickness, substrate_cells + 1))
t_cu = 0.035  # mm, 1 oz copper
if args.thick:  # 2 cells across the thickness, graded out by SmoothMeshLines
    mesh.AddLine("z", [substrate_thickness + t_cu / 2, substrate_thickness + t_cu])
mesh.SmoothMeshLines("all", mesh_res, 1.4)

# copper: PEC with zero thickness as in the patch sim, or a conducting sheet (a skin-effect
# model: finite conductivity, smooth surface, no roughness); --thick: PEC box, thickness only
if args.copper:
    line = CSX.AddConductingSheet("line", conductivity=5.8e7, thickness=35e-6)
    gnd = CSX.AddConductingSheet("gnd", conductivity=5.8e7, thickness=35e-6)
else:
    line = CSX.AddMetal("line")
    gnd = CSX.AddMetal("gnd")
z = substrate_thickness
# the strip between the ports (the ports draw their own part)
line.AddBox(
    priority=10,
    start=[port_len, -line_width / 2, z],
    stop=[length - port_len, line_width / 2, z + (t_cu if args.thick else 0)],
)

substrate = CSX.AddMaterial("substrate", epsilon=substrate_epsR, kappa=substrate_kappa)
substrate.AddBox(
    priority=0,
    start=[0, -board_w / 2, 0],
    stop=[length, board_w / 2, substrate_thickness],
)
gnd.AddBox(priority=10, start=[0, -board_w / 2, 0], stop=[length, board_w / 2, 0])

# MSL ports at both board edges, each with a 50 Ohm feed resistor at the edge (the SMA).
# Port 1 is excited; port 2 points back along -x and stays passive.
ports = []
for nr, x_edge, x_in in ((1, 0, port_len), (2, length, length - port_len)):
    ports.append(
        FDTD.AddMSLPort(
            nr,
            line,
            [x_edge, -line_width / 2, z],
            [x_in, line_width / 2, 0],
            "x",
            "z",
            excite=-1 if nr == 1 else 0,
            Feed_R=feed_R,
            priority=10,
        )
    )

### Run the simulation
t0 = time.time()
FDTD.Run(Sim_Path, cleanup=True)
print("simulation took {:.0f} s".format(time.time() - t0))

### Post-processing
# 1 MHz steps over the VNA span
f = np.linspace(F_MIN, F_MAX, 1501)
# Port waves at the board edges (reference plane shift 0), in the extracted line impedance;
# then renormalised to 50 Ohm like the VNA.
for p in ports:
    p.CalcPort(Sim_Path, f, ref_plane_shift=0)
z_ref = ports[0].Z_ref  # line impedance extracted by port 1
u = [p.uf_tot for p in ports]
i = [p.if_tot for p in ports]
inc = [0.5 * (u[n] + feed_R * i[n]) for n in (0, 1)]
ref = [u[n] - inc[n] for n in (0, 1)]
s11 = ref[0] / inc[0]
s21 = ref[1] / inc[0]
i0 = int(np.argmin(np.abs(f - 5.8e9)))
print(
    "Line impedance at 5.8 GHz: {:.1f} {:+.1f}j Ohm".format(
        z_ref[i0].real, z_ref[i0].imag
    )
)
print(
    "S21 at 5.8 GHz {:.2f} dB, S11 worst over 5.75-5.85 GHz {:.1f} dB".format(
        20 * np.log10(abs(s21[i0])),
        20 * np.log10(np.max(np.abs(s11[(f >= BAND[0]) & (f <= BAND[1])]))),
    )
)

fig, ax = plt.subplots(2, 1, num="S", tight_layout=True, sharex=True)
ax[0].plot(f / 1e9, 20 * np.log10(np.abs(s21)), "k-", linewidth=2, label="S21")
ax[0].plot(f / 1e9, 20 * np.log10(np.abs(s11)), "r--", linewidth=2, label="S11")
ax[0].set_ylabel("S-Parameter (dB)")
ax[0].set_title(
    "{:g} mm line, eps_r {:.2f}, {}".format(
        length,
        substrate_epsR,
        "copper" if args.copper else "PEC, 35 um" if args.thick else "PEC",
    )
)
ax[1].plot(
    f / 1e9,
    np.degrees(unwrap_phase(s21)),
    "k-",
    linewidth=2,
    label="S21 phase (unwrapped)",
)
ax[1].set_ylabel("Phase (deg)")
ax[1].set_xlabel("Frequency (GHz)")
for a in ax:
    a.axvspan(5.75, 5.85, color="g", alpha=0.15)
    a.grid()
    a.set_xmargin(0)
    a.legend()

# save with the data (--all and k8_analyze.py read these), or show
if args.out:
    os.makedirs(args.out, exist_ok=True)
    plt.figure("S").savefig(os.path.join(args.out, tag + ".png"))
    np.savez(
        os.path.join(args.out, tag + ".npz"),
        f=f,
        s11=s11,
        s21=s21,
        z_ref=z_ref,
        length=length,
        eps_r=substrate_epsR,
        h=substrate_thickness,
        copper=args.copper,
        refine=args.refine,
        thick=args.thick,
    )
    # Touchstone v1: reciprocal and symmetric, so S12 = S21 and S22 = S11
    with open(os.path.join(args.out, tag + ".s2p"), "w") as fh:
        fh.write("! openEMS {}\n# HZ S RI R 50\n".format(tag))
        for k in range(len(f)):
            a, b = s11[k], s21[k]
            fh.write(
                "{:.0f} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e}\n".format(
                    f[k], a.real, a.imag, b.real, b.imag, b.real, b.imag, a.real, a.imag
                )
            )
else:
    plt.show()
