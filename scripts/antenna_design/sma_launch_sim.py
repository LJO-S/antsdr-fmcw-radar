# -*- coding: utf-8 -*-
"""
Edge-mount SMA launch (Molex 73251-1150) in openEMS: how it biases the K8 readings

The existing sims drive the 2.868 mm line from an ideal MSL port at the board edge. The
fabricated K8 boards have an edge SMA instead: coax (PTFE, eps_r 2.1) into a pin tab lying on
a 2.29 mm centre pad, body bonded to ground pads on both sides, the line starting 5.0 mm in.
This models that launch.

    python sma_launch_sim.py --kind coax                  # straight coax, calibrates r_o
    python sma_launch_sim.py --kind pair --length 95      # SMA - line - SMA (also 25)
    python sma_launch_sim.py --kind patch                 # K8 patch board with the SMA
    python sma_launch_sim.py --kind patch --port msl      # same mesh, ideal MSL port (control)
    python sma_launch_sim.py --report                     # tables from sma_launch/*.npz (+ k8_analyze.py)
Common: [--eps-r 4.4] [--refine 1.5] [--fine 0.25] [--out DIR] [--mesh-only] [--no-nf2ff]

Excitation: openEMS CoaxialPort (TEM radial field, voltage/current probes), PML at the coax
end(s). S is referred to the SMA mating plane (U_MATE before the board edge, where the VNA
calibration puts it) by moving the port reference plane, in 50 Ohm. Geometry per the KiCad
footprint (pad sizes, positions) and the datasheet drawing outline; SMA parts that the
footprint does not give (flange height, pin and PTFE diameter, leg thickness) are typical
SMA values (results.md, SMA launch). Based on the openEMS "Simple Patch Antenna" tutorial.
"""

### Import Libraries
import argparse, os, tempfile, time
import numpy as np
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))

### SMA geometry, mm. u = distance from the board edge along the line, +u into the board.
R_I, R_O, R_OS = (
    0.625,
    1.875,
    2.375,
)  # pin radius, PTFE radius, outer conductor outer radius (Z 48.9 Ohm here)
EPS_PTFE = 2.1
ZC = 2.125  # coax axis height: pin bottom (ZC - R_I) sits on the board top (1.5)
U_MATE = -9.5  # mating plane (datasheet outline: 9.53 mm from the board edge)
U_FLANGE = -1.75  # flange front face (outline: 1.65 mm), snapped to the mesh lattice
FLANGE_HALF = 4.76  # square flange 9.52 x 9.52 around the axis (outline +-4.76)
TAB_HALF, TAB_T, TAB_END = 0.375, 0.25, 4.75  # flat pin tab on the pad
PAD1_HALF, PAD_END = (
    1.145,
    5.0,
)  # 2.29 mm centre pad; the 5.08 mm of the footprint ends at the line start
LEG_Y, LEG_T, LEG_END = (3.75, 4.76), 0.25, 4.75  # ground legs, top and bottom
PAD2_Y, VIA_Y, VIA_X, VIA_R = (
    (3.17, 5.59),
    4.38,
    6.0,
    0.23,
)  # ground pads and their vias
U_BOX, U_PORT0, U_PORT1, U_MEAS = (
    -16.0,
    -12.5,
    -8.5,
    2.5,
)  # PML end, port start/stop, probes from start
U_CAP = (
    -14.0
)  # --short: coax shorted here (PEC cap 0.5 mm thick), so the nf2ff box cuts no guided wave
U_FINE = 8.0  # fine lattice from U_BOX to here (and the same at the other end)

LINE_W = 2.868  # the feed width of the patch board
H = 1.5
F_BAND = (5.75e9, 5.85e9)
C0_ = 299792458.0

parser = argparse.ArgumentParser(description="Edge SMA launch on FR4 (openEMS)")
parser.add_argument(
    "--report",
    action="store_true",
    help="print the result tables from the saved runs and stop",
)
parser.add_argument("--kind", choices=("coax", "pair", "patch"), default="patch")
parser.add_argument(
    "--port", choices=("sma", "msl"), default="sma", help="msl = ideal port control"
)
parser.add_argument(
    "--length", type=float, default=25.0, help="pair: line length edge to edge, mm"
)
parser.add_argument(
    "--eps-r", type=float, default=4.4, help="eps_r of the substrate (default 4.4)"
)
parser.add_argument(
    "--refine",
    type=float,
    default=1.5,
    help="coarse mesh refinement (1.5 = the tables' mesh)",
)
parser.add_argument(
    "--fine", type=float, default=0.25, help="cell size in the launch region, mm"
)
parser.add_argument("--out", help="save plots and data here instead of showing them")
parser.add_argument(
    "--mesh-only", action="store_true", help="print the cell count and stop"
)
parser.add_argument("--no-nf2ff", action="store_true", help="patch: S11 only")
parser.add_argument(
    "--omit",
    default="",
    help="diagnostic: comma list of legs, pads, vias, flange, tab, taperpad (drop those SMA parts)",
)
parser.add_argument(
    "--short",
    action="store_true",
    help="patch, sma: coax closed by a PEC cap just behind the port instead of PML, so the nf2ff box can be closed "
    "(far field and efficiency only: the port is no longer matched, S11 is not valid)",
)
parser.add_argument(
    "--lattice",
    action="store_true",
    help="msl control: use the SMA runs' fine lattice too (same mesh, no SMA)",
)
parser.add_argument(
    "--post-only",
    action="store_true",
    help="skip the simulation, post-process the existing /tmp run of this tag",
)
parser.add_argument("--nrts", type=int, default=40000)
args = parser.parse_args()
omit = set(filter(None, args.omit.split(",")))
if (
    args.fine < 0.2
):  # the staircase coax is 48.9 Ohm at 0.25 mm; resolved at 0.125 mm, 1.25 / 4.25 mm is 50.7 Ohm
    R_O, R_OS = 2.125, 2.625
if args.out:
    args.out = os.path.abspath(args.out)  # FDTD.Run chdirs into Sim_Path
    plt.switch_backend("Agg")


### --report: tables from the saved runs
def report():
    import subprocess, sys

    out = os.path.join(HERE, "sma_launch")
    ld = lambda n: np.load(os.path.join(out, n + ".npz"), allow_pickle=True)
    dbv = lambda v: 20 * np.log10(np.abs(v))
    band = lambda f: (f >= F_BAND[0]) & (f <= F_BAND[1])
    pr = lambda *a: print(*a)

    pr(
        "| eps_r | table f_min (GHz) | ideal, SMA mesh (GHz) | SMA f_min (GHz) | SMA - table (MHz) | SMA - ideal same mesh (MHz) | SMA S11 min (dB) | eps_r bias vs same mesh |"
    )
    pr("|---|---|---|---|---|---|---|---|")
    shifts, tab, shifts2 = [], {}, []
    fm = lambda e: np.load(
        os.path.join(HERE, "sweep", "d4.40_e{:.2f}_r1.5.npz".format(e))
    )
    for e in (4.2, 4.3, 4.4, 4.5, 4.6):
        t = fm(e)
        tab[e] = t["f"][np.argmin(t["s11_dB"])]
    slope = np.polyfit(list(tab), list(tab.values()), 1)[0]  # Hz per eps_r
    for e in (4.2, 4.4, 4.6):
        d = ld("patch_e{:.2f}_sma_r1.5_f0.25".format(e))
        sh = (float(d["f_min"]) - tab[e]) / 1e6
        shifts.append(sh)
        try:
            c = ld("patch_e{:.2f}_msl_r1.5_f0.25_lat".format(e))
            sh2 = (float(d["f_min"]) - float(c["f_min"])) / 1e6
            shifts2.append(sh2)
            cs, bs = "{:.3f}".format(float(c["f_min"]) / 1e9), "{:+.3f}".format(
                sh2 * 1e6 / slope
            )
            sh2 = "{:+.0f}".format(sh2)
        except OSError:
            cs, sh2, bs = "", "", ""
        pr(
            "| {:.1f} | {:.3f} | {} | {:.3f} | {:+.0f} | {} | {:.1f} | {} |".format(
                e,
                tab[e] / 1e9,
                cs,
                float(d["f_min"]) / 1e9,
                sh,
                sh2,
                float(d["s11_min"]),
                bs,
            )
        )
    pr(
        "mean SMA - table {:+.1f} MHz (spread {:.0f}); SMA - same-mesh ideal {}; table slope {:.0f} MHz per +0.1 eps_r".format(
            np.mean(shifts),
            np.ptp(shifts),
            (
                "{:+.1f} MHz (spread {:.0f})".format(np.mean(shifts2), np.ptp(shifts2))
                if shifts2
                else "n/a"
            ),
            slope / 1e7,
        )
    )
    for nm in ("msl_r1.5_f0.25", "msl_r1.5_f0.25_lat"):
        try:
            c = ld("patch_e4.40_" + nm)
            pr(
                "ideal port 4.4, {}: f_min {:.3f} GHz, {:.1f} dB".format(
                    nm, float(c["f_min"]) / 1e9, float(c["s11_min"])
                )
            )
        except OSError:
            pass
    pr("")
    pr(
        "| fine cell (mm) | ideal port, same mesh f_min (GHz) | SMA f_min (GHz) | SMA - ideal (MHz) | SMA - table 5.801 (MHz) | coax Z (Ohm) |"
    )
    pr("|---|---|---|---|---|---|")
    for fn in ("0.25", "0.1875", "0.125"):
        try:
            a_ = ld("patch_e4.40_sma_r1.5_f" + fn)
            c_ = ld("patch_e4.40_msl_r1.5_f{}_lat".format(fn))
        except OSError:
            continue
        z_ = a_["z_coax"][np.argmin(np.abs(a_["f"] - 5.8e9))].real
        pr(
            "| {} | {:.3f} | {:.3f} | {:+.0f} | {:+.0f} | {:.1f} |".format(
                fn,
                float(c_["f_min"]) / 1e9,
                float(a_["f_min"]) / 1e9,
                (float(a_["f_min"]) - float(c_["f_min"])) / 1e6,
                (float(a_["f_min"]) - tab[4.4]) / 1e6,
                z_,
            )
        )
    for e in (4.2, 4.6):
        try:
            a_ = ld("patch_e{:.2f}_sma_r1.5_f0.1875".format(e))
            pr(
                "0.1875 mm, eps_r {:.1f}: SMA f_min {:.3f} GHz = table {:+.0f} MHz".format(
                    e, float(a_["f_min"]) / 1e9, (float(a_["f_min"]) - tab[e]) / 1e6
                )
            )
        except OSError:
            pass
    try:
        c = ld("patch_e4.40_sma_r1.5_f0.25_no-legs-pads-vias")
        pr(
            "diagnostic, 0.25 mm, SMA without legs/pads/vias: f_min {:.3f} GHz".format(
                float(c["f_min"]) / 1e9
            )
        )
    except OSError:
        pass

    plt.switch_backend("Agg")
    fig, ax = plt.subplots(1, 3, figsize=(15, 4), tight_layout=True)
    for e, c in ((4.2, "C0"), (4.4, "C1"), (4.6, "C2")):
        t = np.load(os.path.join(HERE, "sweep", "d4.40_e{:.2f}_r1.5.npz".format(e)))
        d = ld("patch_e{:.2f}_sma_r1.5_f0.25".format(e))
        ax[0].plot(
            t["f"] / 1e9, t["s11_dB"], c + "--", label="ideal port {:.1f}".format(e)
        )
        ax[0].plot(d["f"] / 1e9, dbv(d["s11"]), c + "-", label="SMA {:.1f}".format(e))
    ax[0].set(
        xlim=(5.5, 6.1),
        ylim=(-50, 0),
        xlabel="GHz",
        ylabel="S11 (dB)",
        title="Patch board",
    )
    for n, st in (("sma", "-"), ("msl", "--")):
        for L, c in ((25, "C0"), (95, "C1")):
            try:
                d = ld("pair{}_e4.40_{}_r1.5_f0.25".format(L, n))
            except OSError:
                continue
            ax[1].plot(
                d["f"] / 1e9,
                dbv(d["s11"]),
                c + st,
                label="{} mm, {}".format(L, "SMA" if n == "sma" else "ideal port"),
            )
    ax[1].axvspan(5.75, 5.85, color="g", alpha=0.15)
    ax[1].set(xlabel="GHz", ylabel="S11 (dB)", title="Line pair")
    try:
        for n, nm in (
            ("patch_e4.40_sma_r1.5_f0.25_short", "SMA"),
            ("patch_e4.40_msl_r1.5_f0.25", "ideal port"),
        ):
            d = ld(n)
            ax[2].plot(d["theta"], d["E_xz"], label=nm + " xz")
            ax[2].plot(d["theta"], d["E_yz"], "--", label=nm + " yz")
        ax[2].set(
            xlabel="theta (deg)", ylabel="dBi", ylim=(-30, 10), title="Patch far field"
        )
    except OSError:
        pass
    for a in ax:
        a.grid()
        a.legend(fontsize=7)
    fig.savefig(os.path.join(out, "summary.png"))

    pr("")
    pr(
        "| case | worst S11 5.75-5.85 GHz (dB) | worst S11 5-6.5 GHz (dB) | S21 at 5.8 GHz (dB) | S11 at 5.8 GHz (dB) |"
    )
    pr("|---|---|---|---|---|")
    for name in (
        "pair25_e4.40_sma_r1.5_f0.25",
        "pair95_e4.40_sma_r1.5_f0.25",
        "pair25_e4.40_msl_r1.5_f0.25",
        "pair95_e4.40_msl_r1.5_f0.25",
        "pair25_e4.40_msl_r1.5_f0.25_lat",
        "pair95_e4.40_msl_r1.5_f0.25_lat",
        "pair25_e4.40_sma_r1.5_f0.125",
        "pair95_e4.40_sma_r1.5_f0.125",
    ):
        try:
            d = ld(name)
        except OSError:
            continue
        f, s11, s21 = d["f"], d["s11"], d["s21"]
        i8 = np.argmin(np.abs(f - 5.8e9))
        pr(
            "| {} | {:.1f} | {:.1f} | {:.2f} | {:.1f} |".format(
                name.replace("_r1.5_f0.25", ""),
                dbv(s11[band(f)]).max(),
                dbv(s11).max(),
                dbv(s21)[i8],
                dbv(s11)[i8],
            )
        )

    pr("")
    for tagpair in (
        ("sma", "pair25_e4.40_sma_r1.5_f0.25", "pair95_e4.40_sma_r1.5_f0.25"),
        ("msl", "pair25_e4.40_msl_r1.5_f0.25", "pair95_e4.40_msl_r1.5_f0.25"),
        (
            "msl, SMA mesh",
            "pair25_e4.40_msl_r1.5_f0.25_lat",
            "pair95_e4.40_msl_r1.5_f0.25_lat",
        ),
        (
            "sma, 0.125 mm",
            "pair25_e4.40_sma_r1.5_f0.125",
            "pair95_e4.40_sma_r1.5_f0.125",
        ),
    ):
        try:
            r = subprocess.run(
                [
                    sys.executable,
                    os.path.join(HERE, "k8_analyze.py"),
                    "--short",
                    os.path.join(out, tagpair[1] + ".s2p"),
                    "--long",
                    os.path.join(out, tagpair[2] + ".s2p"),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        pr("k8_analyze.py, line pair with {} ports:".format(tagpair[0]))
        pr(
            "\n".join(
                l
                for l in r.stdout.splitlines()
                if "eps_eff at 5.8" in l
                or "simulated table" in l
                or "lambda_g" in l
                or "loss:" in l
            )
        )
    pr("(ideal-port table, line_pair_sim.py: eps_eff 3.489, eps_r 4.400)")

    pr("")
    try:
        a, b = ld("patch_e4.40_sma_r1.5_f0.25_short"), ld("patch_e4.40_msl_r1.5_f0.25")
    except OSError:
        return
    pr(
        "far field at each run's S11 minimum: SMA (coax closed, nf2ff box closed) vs ideal port"
    )
    pr(
        "Dmax {:.2f} vs {:.2f} dBi, efficiency {:.1f} vs {:.1f} %".format(
            float(a["Dmax"]),
            float(b["Dmax"]),
            100 * float(a["eta"]),
            100 * float(b["eta"]),
        )
    )
    th = a["theta"]
    for nm, key in (("xz", "E_xz"), ("yz", "E_yz")):
        pr(
            "{} pattern (dBi) at theta 0 / +-90 / 180: SMA {:.1f} / {:.1f},{:.1f} / {:.1f}; ideal {:.1f} / {:.1f},{:.1f} / {:.1f}".format(
                nm,
                *[a[key][np.argmin(np.abs(th - t))] for t in (0, -90, 90, 180)],
                *[b[key][np.argmin(np.abs(th - t))] for t in (0, -90, 90, 180)]
            )
        )
    # complex far fields: scale the ideal-port field onto the SMA one (best complex factor), residual re beam peak
    ea = np.concatenate([a["E_theta"].ravel(), a["E_phi"].ravel()])
    eb = np.concatenate([b["E_theta"].ravel(), b["E_phi"].ravel()])
    c = np.vdot(eb, ea) / np.vdot(eb, eb)
    res_ = ea - c * eb
    pr(
        "pattern residual after the best complex scale: peak {:.1f} dB, rms {:.1f} dB re the beam peak".format(
            20 * np.log10(np.abs(res_).max() / np.abs(ea).max()),
            20 * np.log10(np.sqrt(np.mean(np.abs(res_) ** 2)) / np.abs(ea).max()),
        )
    )


if args.report:
    report()
    raise SystemExit(0)

from CSXCAD import ContinuousStructure
from openEMS import openEMS
from openEMS.physical_constants import *

eps_r = args.eps_r
tag = "{}{}_e{:.2f}_{}_r{:g}_f{:g}{}".format(
    args.kind,
    "{:g}".format(args.length) if args.kind == "pair" else "",
    eps_r,
    args.port,
    args.refine,
    args.fine,
    "_short" if args.short else "_lat" if args.lattice else "",
)
if args.kind == "coax":
    tag = "coax_f{:g}".format(args.fine)
if omit:
    tag += "_no-" + "-".join(sorted(omit))
Sim_Path = os.path.join(tempfile.gettempdir(), "SMA_" + tag)
unit = 1e-3

if args.kind == "patch":
    f0, fc = 5.8e9, 2e9  # as Simple_Patch_Antenna.py
    f_grid = np.linspace(f0 - fc, f0 + fc, 4001)
else:
    f0, fc = 5.75e9, 1.5e9  # as line_pair_sim.py
    f_grid = np.linspace(5.0e9, 6.5e9, 1501)
kappa = (
    0.02 * 2 * np.pi * 5.8e9 * EPS0 * eps_r
)  # tan d = 0.02 at 5.8 GHz, as both scripts
design_eps = (
    4.4  # geometry and mesh are those of the 4.4 design (a fixed-geometry sweep)
)
# lambda/20 in the substrate at the highest simulated frequency, as the existing scripts
mesh_res = C0 / (f0 + fc) / unit / np.sqrt(design_eps) / 20 / args.refine
edge_res = mesh_res / 2
fine = args.fine

# patch geometry = results.md "board to fabricate" (Simple_Patch_Antenna.py TUNED[4.4])
P_L, P_INSET, GND_MARGIN, INSET_GAP = 11.842, 3.70, 20.0, 1.0
P_W = C0 / (2 * 5.8e9) * np.sqrt(2 / (design_eps + 1)) / unit  # 15.728
board_x = P_L + 2 * GND_MARGIN  # 51.842
board_y = P_W + 2 * GND_MARGIN  # 55.728
x_patch, x_inset = GND_MARGIN, GND_MARGIN + P_INSET

if args.kind == "pair":
    length, board_w = args.length, 15.0
elif args.kind == "patch":
    length, board_w = board_x, board_y
else:
    length, board_w = 2 * 16.0, 0.0
two_ends = args.kind in ("pair", "coax")
sma = args.port == "sma"

# Air around the board, local to the kind
if args.kind == "patch":
    Box = dict(
        x=(U_BOX if sma else -16.0, length + 18),
        y=(-board_w / 2 - 18, board_w / 2 + 18),
        z=(-14.0, H + 26),
    )
elif args.kind == "pair":
    Box = dict(
        x=(U_BOX, length - U_BOX),
        y=(-board_w / 2 - 15, board_w / 2 + 15),
        z=(-15.0, H + 25),
    )
else:
    Box = dict(x=(U_BOX, length - U_BOX), y=(-3.2, 3.2), z=(ZC - 3.2, ZC + 3.2))

### FDTD setup
FDTD = openEMS(NrTS=args.nrts, EndCriteria=1e-4)
FDTD.SetGaussExcite(f0, fc)
bc = ["MUR"] * 6
if args.kind == "coax":
    bc[2:] = ["PEC"] * 4
if sma and not args.short:
    bc[0] = "PML_8"
    if two_ends:
        bc[1] = "PML_8"
FDTD.SetBoundaryCond(bc)

CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(unit)


def ends():
    """(x_edge, d) of each SMA: d = +1 where the board lies at +x."""
    return [(0.0, +1)] + ([(length, -1)] if two_ends else [])


def lattice(lo, hi, off=0.0):
    k0, k1 = int(np.ceil((lo - off) / fine - 1e-9)), int(
        np.floor((hi - off) / fine + 1e-9)
    )
    return np.arange(k0, k1 + 1) * fine + off


EXCL = {
    "x": [],
    "y": [],
    "z": [],
}  # no lattice line inside a thirds-rule cell (it would split the cell holding the edge)


def merged(req, lat, d):
    """Required lines plus the lattice lines that are not within 0.4 cells of one or inside a thirds cell."""
    req = np.array(sorted(set(np.round(req, 6))))
    return np.concatenate(
        [
            req,
            [
                v
                for v in lat
                if np.min(np.abs(req - v)) > 0.4 * fine
                and not any(a - 0.4 * fine < v < b + 0.4 * fine for a, b in EXCL[d])
            ],
        ]
    )


def thirds(pos, metal_side, d):
    """Thirds rule: one mesh line 1/3 inside the metal edge, one 2/3 outside (metal_side +-1)."""
    ln = [pos + metal_side * edge_res / 3, pos - metal_side * 2 * edge_res / 3]
    EXCL[d].append((min(ln), max(ln)))
    return ln


# required lines per direction
rx = [Box["x"][0], Box["x"][1], 0.0, length]
ry = [Box["y"][0], Box["y"][1]]
rz = [Box["z"][0], Box["z"][1], 0.0, H]
lx = []
if board_w:
    ry += [-board_w / 2, board_w / 2]
if args.kind != "coax":
    for s in (+1, -1):
        ry += thirds(s * LINE_W / 2, -s, "y")
if args.kind == "patch":
    rx += (
        thirds(x_patch, +1, "x")
        + thirds(x_inset, +1, "x")
        + thirds(x_patch + P_L, -1, "x")
    )
    for s in (+1, -1):
        ry += thirds(s * (LINE_W / 2 + INSET_GAP), +s, "y") + thirds(
            s * P_W / 2, -s, "y"
        )
if sma:
    for x_edge, d in ends():
        X = lambda u: x_edge + d * u
        rx += [
            X(u)
            for u in (U_BOX, U_PORT0, U_PORT1, U_FLANGE, 0.0, TAB_END, PAD_END, VIA_X)
        ]
        lx.append(sorted((X(U_BOX), X(U_FINE))))
    ry += [s * v for s in (-1, 1) for v in (R_I, R_O, R_OS)]
    rz += [ZC - R_OS, ZC + R_OS, ZC - R_O, ZC + R_O, ZC + R_I]
    if args.kind != "coax":
        ry += [
            s * v
            for s in (-1, 1)
            for v in (
                TAB_HALF,
                PAD1_HALF,
                LEG_Y[0],
                LEG_Y[1],
                PAD2_Y[0],
                PAD2_Y[1],
                VIA_Y - VIA_R,
                VIA_Y + VIA_R,
                FLANGE_HALF,
            )
        ]
        rz += [-LEG_T, H + TAB_T, ZC - FLANGE_HALF, ZC + FLANGE_HALF]
lat_on = sma or args.lattice
if args.lattice and not sma:  # same fine windows as the SMA run, at both board edges
    for x_edge, d in ends():
        lx.append(sorted((x_edge - d * 16.0, x_edge + d * U_FINE)))
fx = np.concatenate([lattice(a, b) for a, b in lx]) if lx else np.array([])
fy = (
    lattice(-6.1, 6.1, off=0.5 * fine if fine > 0.2 else 0.0)
    if lat_on
    else np.array([])
)  # pin/PTFE edges on the lattice
fz = lattice(-0.5, 4.75) if lat_on else np.array([])
mesh.AddLine("x", merged(rx, fx, "x"))
mesh.AddLine("y", merged(ry, fy, "y"))
mesh.AddLine("z", merged(rz, fz, "z"))
if not lat_on:  # control: same substrate cell count rule as the existing scripts
    mesh.AddLine("z", np.linspace(0, H, round(4 * args.refine) + 1))
mesh.SmoothMeshLines("all", mesh_res, 1.4)
nxyz = [len(mesh.GetLines(n)) for n in range(3)]
print(
    "{} port {} eps_r {:.2f}: mesh {} x {} x {} = {:.2f} M cells (coarse {:.3f}, fine {:g} mm), min dx {:.3f}".format(
        args.kind,
        args.port,
        eps_r,
        *nxyz,
        np.prod(nxyz) / 1e6,
        mesh_res,
        fine,
        min(np.min(np.diff(mesh.GetLines(n))) for n in range(3))
    ),
    flush=True,
)
if args.mesh_only:
    raise SystemExit(0)

### Structures
cu = CSX.AddMetal(
    "cu"
)  # PEC copper, zero thickness as the existing sims (SMA parts have thickness)
ptfe = CSX.AddMaterial("ptfe", epsilon=EPS_PTFE)
z = H


def strip(x1, x2, w, pr=10):
    cu.AddBox(priority=pr, start=[x1, -w / 2, z], stop=[x2, w / 2, z])


if args.kind != "coax":
    fr4 = CSX.AddMaterial("substrate", epsilon=eps_r, kappa=kappa)
    fr4.AddBox(priority=0, start=[0, -board_w / 2, 0], stop=[length, board_w / 2, H])
    cu.AddBox(
        priority=10, start=[0, -board_w / 2, 0], stop=[length, board_w / 2, 0]
    )  # ground

if args.kind == "pair":
    strip(PAD_END, length - PAD_END, LINE_W)
elif args.kind == "patch":
    strip(PAD_END, x_inset, LINE_W)
    cu.AddBox(
        priority=10, start=[x_inset, -P_W / 2, z], stop=[x_patch + P_L, P_W / 2, z]
    )
    for s in (+1, -1):
        cu.AddBox(
            priority=10,
            start=[x_patch, s * (LINE_W / 2 + INSET_GAP), z],
            stop=[x_inset, s * P_W / 2, z],
        )

ports = []
s21 = None
if sma:
    for nr, (x_edge, d) in enumerate(ends(), 1):

        def xs(u1, u2):
            a, b = sorted((x_edge + d * u1, x_edge + d * u2))
            return a, b

        def box(prop, u1, u2, y1, y2, z1, z2, pr):
            a, b = xs(u1, u2)
            prop.AddBox(priority=pr, start=[a, y1, z1], stop=[b, y2, z2])

        def cyl(prop, u1, u2, r, pr, y=0.0, zc=ZC):
            prop.AddCylinder(
                priority=pr,
                start=[x_edge + d * u1, y, zc],
                stop=[x_edge + d * u2, y, zc],
                radius=r,
            )

        if args.kind != "coax":
            # coax from the PML end to the flange: PTFE, outer shell, pin (the port draws U_PORT0..1 again)
            ub = U_CAP if args.short else U_BOX  # where the coax starts
            cyl(ptfe, ub, 0.0, R_O, 20)
            if args.short:
                box(
                    cu, U_CAP - 0.5, U_CAP, -R_OS, R_OS, ZC - R_OS, ZC + R_OS, 40
                )  # PEC cap behind the port
            for u1, u2 in ((ub, U_PORT0), (U_PORT1, U_FLANGE)):
                a, b = xs(u1, u2)
                cu.AddCylindricalShell(
                    start=[a, 0, ZC],
                    stop=[b, 0, ZC],
                    radius=0.5 * (R_O + R_OS),
                    shell_width=R_OS - R_O,
                    priority=30,
                )
            cyl(cu, ub, 0.0, R_I, 30)
            if "flange" not in omit:
                box(
                    cu,
                    U_FLANGE,
                    0.0,
                    -FLANGE_HALF,
                    FLANGE_HALF,
                    ZC - FLANGE_HALF,
                    ZC + FLANGE_HALF,
                    10,
                )  # flange, PTFE fills its bore
            # pin tab on the centre pad, ground legs and pads, vias
            if "tab" not in omit:
                box(cu, -0.25, TAB_END, -TAB_HALF, TAB_HALF, H, H + TAB_T, 30)
            box(cu, 0.0, PAD_END, -PAD1_HALF, PAD1_HALF, H, H, 10)
            for s in (+1, -1):
                y1, y2 = sorted((s * LEG_Y[0], s * LEG_Y[1]))
                if "legs" not in omit:
                    box(cu, 0.0, LEG_END, y1, y2, H, H + LEG_T, 10)  # top leg
                    box(cu, 0.0, LEG_END, y1, y2, -LEG_T, 0.0, 10)  # bottom leg
                y1, y2 = sorted((s * PAD2_Y[0], s * PAD2_Y[1]))
                if "pads" not in omit:
                    box(cu, 0.0, PAD_END + 0.08, y1, y2, H, H, 10)  # top ground pad
                    box(
                        cu,
                        PAD_END + 0.06,
                        VIA_X + 0.01,
                        s * (VIA_Y - 0.23),
                        s * (VIA_Y + 0.23),
                        H,
                        H,
                        10,
                    )  # small pad to the via
                if "vias" not in omit:
                    a, b = xs(VIA_X, VIA_X)
                    cu.AddCylinder(
                        priority=10,
                        start=[a, s * VIA_Y, 0],
                        stop=[a, s * VIA_Y, H],
                        radius=VIA_R,
                    )
        elif nr == 1:  # straight coax through the whole box
            xa, xb = Box["x"]
            for prop, r, pr in ((ptfe, R_O, 20), (cu, R_I, 30)):
                prop.AddCylinder(
                    priority=pr, start=[xa, 0, ZC], stop=[xb, 0, ZC], radius=r
                )
            cu.AddCylindricalShell(
                start=[xa, 0, ZC],
                stop=[xb, 0, ZC],
                radius=0.5 * (R_O + R_OS),
                shell_width=R_OS - R_O,
                priority=30,
            )
        p = FDTD.AddCoaxialPort(
            nr,
            cu,
            ptfe,
            [x_edge + d * U_PORT0, 0, ZC],
            [x_edge + d * U_PORT1, 0, ZC],
            "x",
            R_I,
            R_O,
            R_OS,
            excite=1 if nr == 1 else 0,
            priority=30,
            MeasPlaneShift=U_MEAS,
        )
        ports.append(p)
else:
    # ideal MSL port at the board edge, as Simple_Patch_Antenna.py / line_pair_sim.py
    x_in = x_patch if args.kind == "patch" else 8.0
    ports.append(
        FDTD.AddMSLPort(
            1,
            cu,
            [0, -LINE_W / 2, z],
            [x_in, LINE_W / 2, 0],
            "x",
            "z",
            excite=-1,
            Feed_R=50,
            priority=10,
        )
    )
    if args.kind == "pair":
        ports.append(
            FDTD.AddMSLPort(
                2,
                cu,
                [length, -LINE_W / 2, z],
                [length - 8.0, LINE_W / 2, 0],
                "x",
                "z",
                excite=0,
                Feed_R=50,
                priority=10,
            )
        )
        strip(8.0, length - 8.0, LINE_W)

nf2ff = None
if args.kind == "patch" and not args.no_nf2ff:
    # the coax passes the -x face: skip it (a guided wave cut by the box would "radiate")
    nf2ff = FDTD.CreateNF2FFBox(
        directions=[0 if (sma and not args.short) else 1, 1, 1, 1, 1, 1]
    )

### Run the simulation
t0 = time.time()
if not args.post_only:
    FDTD.Run(Sim_Path, cleanup=True)
print("simulation took {:.0f} s".format(time.time() - t0), flush=True)

### Post-processing
f = f_grid
i0 = int(np.argmin(np.abs(f - 5.8e9)))
band = (f >= F_BAND[0]) & (f <= F_BAND[1])
db = lambda v: 20 * np.log10(np.abs(v))
res = dict(
    f=f,
    eps_r=eps_r,
    refine=args.refine,
    fine=fine,
    kind=args.kind,
    port=args.port,
    length=length,
)
if sma:
    for p in ports:
        p.CalcPort(Sim_Path, f)
    z_ref = [p.Z_ref.copy() for p in ports]
    print(
        "Coax impedance at 5.8 GHz: {}".format(
            " / ".join(
                "{:.2f} {:+.2f}j Ohm".format(zr[i0].real, zr[i0].imag) for zr in z_ref
            )
        )
    )
    for p in ports:  # S in 50 Ohm at the mating plane
        p.CalcPort(Sim_Path, f, ref_impedance=50.0, ref_plane_shift=U_MATE - U_PORT0)
    res["z_coax"] = z_ref[0]
    s11 = ports[0].uf_ref / ports[0].uf_inc
    s21 = (
        ports[1].uf_ref / ports[0].uf_inc if two_ends else None
    )  # the wave leaving port 2 / entering port 1
    p_acc = ports[0].P_acc
    # same, normalised to the coax's own (numerical) impedance: no mismatch from the staircase coax
    for p, zr in zip(ports, z_ref):
        p.CalcPort(Sim_Path, f, ref_impedance=zr.real, ref_plane_shift=U_MATE - U_PORT0)
    res["s11z"] = ports[0].uf_ref / ports[0].uf_inc
    if two_ends:
        res["s21z"] = ports[1].uf_ref / ports[0].uf_inc
else:
    if args.kind == "patch":
        ports[0].CalcPort(Sim_Path, f, ref_plane_shift=0)
        z_line = ports[0].Z_ref[i0]
        z_edge = ports[0].uf_tot / ports[0].if_tot
        s11 = (z_edge - 50) / (z_edge + 50)
        p_acc = ports[0].P_acc
        print(
            "Line impedance at 5.8 GHz: {:.1f} {:+.1f}j Ohm".format(
                z_line.real, z_line.imag
            )
        )
    else:
        for p in ports:
            p.CalcPort(Sim_Path, f, ref_plane_shift=0)
        u = [p.uf_tot for p in ports]
        i = [p.if_tot for p in ports]
        inc = [0.5 * (u[n] + 50 * i[n]) for n in (0, 1)]
        s11, s21 = (u[0] - inc[0]) / inc[0], (u[1] - inc[1]) / inc[0]
res["s11"] = s11
if s21 is not None:
    res["s21"] = s21

s11_dB = db(s11)
if args.kind == "patch":
    k = int(np.argmin(s11_dB))
    # parabolic refinement of the minimum on the 1 MHz grid (dB), as a check on the grid reading
    if 0 < k < len(f) - 1:
        a, b, c = s11_dB[k - 1], s11_dB[k], s11_dB[k + 1]
        f_min_p = f[k] + 0.5 * (a - c) / (a - 2 * b + c) * (f[1] - f[0])
    else:
        f_min_p = f[k]
    lo = hi = k
    while lo > 0 and s11_dB[lo - 1] < -10:
        lo -= 1
    while hi < len(f) - 1 and s11_dB[hi + 1] < -10:
        hi += 1
    if "s11z" in res:
        kz = int(np.argmin(db(res["s11z"])))
        res.update(f_min_z=f[kz], s11_min_z=db(res["s11z"])[kz])
        print(
            "normalised to the coax's own impedance: S11 min {:.1f} dB at {:.3f} GHz".format(
                db(res["s11z"])[kz], f[kz] / 1e9
            )
        )
    res.update(
        f_min=f[k],
        s11_min=s11_dB[k],
        f_min_p=f_min_p,
        band10=(f[lo], f[hi]),
        worst_band=np.max(s11_dB[band]),
    )
    print(
        "S11 min {:.1f} dB at {:.3f} GHz (parabolic {:.4f}); -10 dB band {:.3f}-{:.3f} GHz; worst over 5.75-5.85 GHz {:.1f} dB".format(
            s11_dB[k],
            f[k] / 1e9,
            f_min_p / 1e9,
            f[lo] / 1e9,
            f[hi] / 1e9,
            np.max(s11_dB[band]),
        )
    )
    if nf2ff is not None:
        theta = np.arange(-180.0, 180.0, 2.0)
        r = nf2ff.CalcNF2FF(
            Sim_Path,
            f[k],
            theta,
            [0.0, 90.0],
            center=[(x_patch + P_L / 2) * 1e-3, 0, 1e-3],
        )
        Dmax = 10 * np.log10(r.Dmax[0])
        eta = r.Prad[0] / p_acc[k]
        E = 20 * np.log10(r.E_norm[0] / np.max(r.E_norm[0])) + Dmax  # dBi
        ia = np.argmin(np.abs(theta + 90))  # theta -90, phi 0: towards -x, the SMA side
        res.update(
            theta=theta,
            E_xz=E[:, 0],
            E_yz=E[:, 1],
            Dmax=Dmax,
            eta=eta,
            E_theta=r.E_theta[0],
            E_phi=r.E_phi[0],
        )
        print(
            "Dmax {:.2f} dBi, efficiency {:.1f} %, gain {:.2f} dBi; xz pattern at theta -90 (SMA side) {:.1f} dBi, at +90 {:.1f} dBi, at 180 {:.1f} dBi".format(
                Dmax,
                100 * eta,
                Dmax + 10 * np.log10(eta),
                E[ia, 0],
                E[np.argmin(np.abs(theta - 90)), 0],
                E[np.argmin(np.abs(theta - 180)), 0],
            )
        )
else:
    print(
        "S11 worst over 5.75-5.85 GHz {:.1f} dB, over 5-6.5 GHz {:.1f} dB".format(
            np.max(s11_dB[band]), np.max(s11_dB)
        )
    )
    if "s21" in res:
        print("S21 at 5.8 GHz {:.2f} dB".format(db(res["s21"])[i0]))

fig, ax = plt.subplots(num="S", tight_layout=True)
ax.plot(f / 1e9, s11_dB, "k-", label="S11")
if "s21" in res:
    ax.plot(f / 1e9, db(res["s21"]), "r--", label="S21")
ax.axvspan(5.75, 5.85, color="g", alpha=0.15)
ax.set_xlabel("Frequency (GHz)")
ax.set_ylabel("S-Parameter (dB)")
ax.set_title(tag)
ax.grid()
ax.legend()

if args.out:
    os.makedirs(args.out, exist_ok=True)
    fig.savefig(os.path.join(args.out, tag + ".png"))
    np.savez(os.path.join(args.out, tag + ".npz"), **res)
    if args.kind == "patch":  # Touchstone v1, 1 port
        with open(os.path.join(args.out, tag + ".s1p"), "w") as fh:
            fh.write("! openEMS {}\n# HZ S RI R 50\n".format(tag))
            for k in range(len(f)):
                fh.write(
                    "{:.0f} {:.9e} {:.9e}\n".format(f[k], s11[k].real, s11[k].imag)
                )
    elif "s21" in res:  # reciprocal, mirror-symmetric: S12 = S21, S22 = S11
        with open(os.path.join(args.out, tag + ".s2p"), "w") as fh:
            fh.write("! openEMS {}\n# HZ S RI R 50\n".format(tag))
            for k in range(len(f)):
                a, b = res["s11"][k], res["s21"][k]
                fh.write(
                    "{:.0f} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e} {:.9e}\n".format(
                        f[k],
                        a.real,
                        a.imag,
                        b.real,
                        b.imag,
                        b.real,
                        b.imag,
                        a.real,
                        a.imag,
                    )
                )
else:
    plt.show()
