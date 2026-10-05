# -*- coding: utf-8 -*-
"""
Inset-fed patch antenna, 5.8 GHz on FR4 (Part K8, guide 9.3)

Based on the openEMS "Simple Patch Antenna" tutorial, with the probe feed replaced by a
50 Ohm microstrip line from the board edge into an inset notch: the board as it will be
fabricated, with the edge SMA at the port.

Tested with
 - python 3.14
 - openEMS v0.37

(c) 2015-2023 Thorsten Liebig <thorsten.liebig@gmx.de>
    04-Jan-2026: modified to use matplotlib.pyplot instead of pylab

"""

### Import Libraries
import argparse, os, tempfile
import numpy as np
import matplotlib.pyplot as plt  # pip install matplotlib

from CSXCAD import ContinuousStructure
from openEMS import openEMS
from openEMS.physical_constants import *

### General parameter setup
# Hand-tuned per design eps_r (results.md): resonant length L and inset depth, mm.
# W and the 50 Ohm line width follow from the formulas below. Tuned for one h only:
# 4.4 at h 1.5 mm (the board to fabricate); 4.2 and 4.6 at h 1.6 mm, now starting points.
TUNED = {4.2: (12.133, 3.73), 4.4: (11.842, 3.70), 4.6: (11.528, 3.67)}

parser = argparse.ArgumentParser(description="Inset-fed 5.8 GHz patch on FR4 (openEMS)")
parser.add_argument(
    "--design",
    type=float,
    default=4.4,
    choices=sorted(TUNED),
    help="eps_r the geometry is designed for (default 4.4)",
)
parser.add_argument(
    "--eps-r",
    type=float,
    help="eps_r of the simulated substrate (default: --design). Set it apart from "
    "--design to see what another board does to a fixed geometry",
)
parser.add_argument("--out", help="save plots and S11 data here instead of showing them")
parser.add_argument(
    "--refine",
    type=float,
    default=1.0,
    help="mesh refinement factor, xy and substrate cells together (convergence check)",
)
args = parser.parse_args()
if args.out:
    args.out = os.path.abspath(args.out)  # FDTD.Run chdirs into Sim_Path
    plt.switch_backend("Agg")

design_epsR = args.design  # sets the geometry
substrate_epsR = args.design if args.eps_r is None else args.eps_r  # the material simulated

# One tag per (geometry, substrate, mesh): names the sim directory and the saved results
tag = "d{:.2f}_e{:.2f}_r{:g}".format(design_epsR, substrate_epsR, args.refine)
Sim_Path = os.path.join(tempfile.gettempdir(), "Patch_Inset_" + tag)

post_proc_only = False  # set to True to skip the simulation and only do post-processing
GUI = False  # set to True to show the CSXCAD GUI for debugging

f0 = 5.8e9  # center frequency
unit = 1e-3  # all dimensions in mm

# substrate setup (FR4 is 4.2-4.6 in practice, Rogers 5880 2.2)
substrate_kappa = 0.02 * 2 * np.pi * f0 * EPS0 * substrate_epsR  # tan(delta) = 0.02
substrate_thickness = 1.5  # h: dielectric only. JLCPCB 2-layer "1.6 mm" = 1.5 mm core + copper
substrate_cells = round(4 * args.refine)


# --------------------
# Patch dimensions (Balanis transmission-line model, all lengths in mm)
# --------------------
# 1. Width W (non-resonant dimension)
#  W = c / (2 * f0) * sqrt(2 / (epsR + 1))
W = C0 / (2 * f0) * np.sqrt(2 / (design_epsR + 1)) / unit

# 2. Effective dielectric constant (valid for W / h > 1)
#  eps_eff = (epsR + 1) / 2 + (epsR - 1) / 2 * (1 + 12 * h / W) ** (-0.5)
eps_eff = (substrate_epsR + 1) / 2 + (substrate_epsR - 1) / 2 * (
    1 + 12 * substrate_thickness / W
) ** (-0.5)

# 3. Fringing length extension
#  delta_L = 0.412 * h * (eps_eff + 0.3) * (W / h + 0.264) / ((eps_eff - 0.258) * (W / h + 0.8))
delta_L = (
    0.412
    * substrate_thickness
    * (eps_eff + 0.3)
    * (W / substrate_thickness + 0.264)
    / ((eps_eff - 0.258) * (W / substrate_thickness + 0.8))
)

# 4. Physical length L (resonant dimension) reference
#  L = c / (2 * f0 * sqrt(eps_eff)) - 2 * delta_L
L = C0 / (2 * f0 * np.sqrt(eps_eff)) / unit - 2 * delta_L

# In this script x is the resonant direction (the feed runs along x),
# so Balanis' L goes in x and W goes in y.
# patch width (resonant length) in x-direction
patch_width = TUNED[design_epsR][0]
print("Theoretical L = {:.3f} mm vs actual L = {:.3f} mm".format(L, patch_width))
# patch length in y-direction
patch_length = W

# substrate / ground plane with a margin around the patch
# (rule of thumb minimum is ~3*h per side; more makes the result less ground-size dependent)
gnd_margin = 20
substrate_width = patch_width + 2 * gnd_margin
substrate_length = patch_length + 2 * gnd_margin

# --------------------
# Feed: 50 Ohm microstrip from the board edge (-x) into an inset notch
# --------------------
feed_R = 50  # port and reference impedance


def msl_width(z0, eps_r, h):
    """Microstrip width for impedance z0 (Pozar 3.197, Hammerstad synthesis)."""
    A = z0 / 60 * np.sqrt((eps_r + 1) / 2) + (eps_r - 1) / (eps_r + 1) * (
        0.23 + 0.11 / eps_r
    )
    w_h = 8 * np.exp(A) / (np.exp(2 * A) - 2)
    if w_h > 2:
        B = 377 * np.pi / (2 * z0 * np.sqrt(eps_r))
        w_h = (
            2
            / np.pi
            * (
                B
                - 1
                - np.log(2 * B - 1)
                + (eps_r - 1) / (2 * eps_r) * (np.log(B - 1) + 0.39 - 0.61 / eps_r)
            )
        )
    return w_h * h


feed_width = msl_width(feed_R, design_epsR, substrate_thickness)
# Inset depth from the patch edge. Inset feeds follow Rin(y0) ~ R_edge * cos^4(pi * y0 / L)
# (Basilio et al. 2001), not the probe's cos^2. Tune with the hint printed at the end.
inset_depth = TUNED[design_epsR][1]
inset_gap = 1.0  # notch clearance each side of the line

x_board = -substrate_width / 2  # board edge: SMA, port and feed resistor
x_patch = -patch_width / 2  # radiating edge on the feed side
x_inset = x_patch + inset_depth  # feed point

print(
    "W = {:.3f} mm, eps_R = {:.4f} (design {:.2f}), eps_eff = {:.4f}, delta_L = {:.4f} mm, "
    "L = {:.3f} mm, 50 Ohm line = {:.3f} mm".format(
        patch_length,
        substrate_epsR,
        design_epsR,
        eps_eff,
        delta_L,
        patch_width,
        feed_width,
    )
)

# size of the simulation box
SimBox = np.array([110, 110, 90])

# setup FDTD parameter & excitation function
fc = 2e9  # 20 dB corner frequency

### FDTD setup
## * Limit the simulation to 30k timesteps
## * Define a reduced end criteria of -40dB
FDTD = openEMS(NrTS=30000, EndCriteria=1e-4)
FDTD.SetGaussExcite(f0, fc)
FDTD.SetBoundaryCond(["MUR", "MUR", "MUR", "MUR", "MUR", "MUR"])


CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(unit)
# lambda/20 inside the substrate at the highest simulated frequency. From the design eps_r,
# so every run of an eps_r sweep shares one mesh and only the material changes.
mesh_res = C0 / (f0 + fc) / unit / np.sqrt(design_epsR) / 20 / args.refine
edge_res = mesh_res / 2


def add_metal_edge(direction, pos, metal_side):
    """Thirds rule: one mesh line 1/3 inside the metal edge, one 2/3 outside (metal_side +-1)."""
    mesh.AddLine(
        direction,
        [pos + metal_side * edge_res / 3, pos - metal_side * 2 * edge_res / 3],
    )


### Generate properties, primitives and mesh-grid
# Manual mesh: the MSL port needs the grid to exist before it is created.
mesh.AddLine("x", [-SimBox[0] / 2, SimBox[0] / 2, x_board, substrate_width / 2])
mesh.AddLine(
    "y", [-SimBox[1] / 2, SimBox[1] / 2, -substrate_length / 2, substrate_length / 2]
)
mesh.AddLine("z", [-SimBox[2] / 3, SimBox[2] * 2 / 3])
add_metal_edge("x", x_patch, +1)
add_metal_edge("x", x_inset, +1)
add_metal_edge("x", patch_width / 2, -1)
for s in (+1, -1):
    add_metal_edge("y", s * feed_width / 2, -s)
    add_metal_edge("y", s * (feed_width / 2 + inset_gap), +s)
    add_metal_edge("y", s * patch_length / 2, -s)
# add extra cells to discretize the substrate thickness
mesh.AddLine("z", np.linspace(0, substrate_thickness, substrate_cells + 1))
mesh.SmoothMeshLines("all", mesh_res, 1.4)

# create patch: body beyond the feed point plus two fingers either side of the notch
patch = CSX.AddMetal("patch")  # create a perfect electric conductor (PEC)
z = substrate_thickness
patch.AddBox(
    priority=10,
    start=[x_inset, -patch_length / 2, z],
    stop=[patch_width / 2, patch_length / 2, z],
)
for s in (+1, -1):
    patch.AddBox(
        priority=10,
        start=[x_patch, s * (feed_width / 2 + inset_gap), z],
        stop=[x_inset, s * patch_length / 2, z],
    )
# feed line inside the notch (the port below draws the rest, out to the board edge)
patch.AddBox(
    priority=10, start=[x_patch, -feed_width / 2, z], stop=[x_inset, feed_width / 2, z]
)

# create substrate
substrate = CSX.AddMaterial("substrate", epsilon=substrate_epsR, kappa=substrate_kappa)
start = [-substrate_width / 2, -substrate_length / 2, 0]
stop = [substrate_width / 2, substrate_length / 2, substrate_thickness]
substrate.AddBox(priority=0, start=start, stop=stop)

# create ground (same size as substrate)
gnd = CSX.AddMetal("gnd")  # create a perfect electric conductor (PEC)
start[2] = 0
stop[2] = 0
gnd.AddBox(start, stop, priority=10)

# MSL port on the uniform line from the board edge to the patch: excitation and a 50 Ohm
# feed resistor at the board edge (the SMA), voltage/current probes halfway along the line.
port = FDTD.AddMSLPort(
    1,
    patch,
    [x_board, -feed_width / 2, substrate_thickness],
    [x_patch, feed_width / 2, 0],
    "x",
    "z",
    excite=-1,
    Feed_R=feed_R,
    priority=10,
)

# Add the nf2ff recording box
nf2ff = FDTD.CreateNF2FFBox()

### Run the simulation
if GUI:  # debugging only
    CSX_file = os.path.join(Sim_Path, "patch_inset.xml")
    if not os.path.exists(Sim_Path):
        os.mkdir(Sim_Path)
    CSX.Write2XML(CSX_file)
    from CSXCAD import AppCSXCAD_BIN

    os.system(AppCSXCAD_BIN + ' "{}"'.format(CSX_file))

if not post_proc_only:
    FDTD.Run(Sim_Path, cleanup=True)


### Post-processing and plotting
# 1 MHz steps: the resonance is read off this grid, and an eps_r sweep reads it to the MHz
f = np.linspace(max(1e9, f0 - fc), f0 + fc, 4001)
# Zin at the feed point (reference plane moved along the extracted line) for inset tuning.
port.CalcPort(Sim_Path, f, ref_plane_shift=x_inset - x_board)
Zin = port.uf_tot / port.if_tot
# S11 at the board edge vs 50 Ohm, as the VNA sees it: the line is not exactly 50 Ohm, so
# it transforms the feed-point match.
port.CalcPort(Sim_Path, f, ref_plane_shift=0)
Z_line = port.Z_ref[np.argmin(np.abs(f - f0))]
Z_edge = port.uf_tot / port.if_tot
s11 = (Z_edge - feed_R) / (Z_edge + feed_R)
s11_dB = 20.0 * np.log10(np.abs(s11))
print(
    "Line impedance at {:.2f} GHz: {:.1f} {:+.1f}j Ohm".format(
        f0 / 1e9, Z_line.real, Z_line.imag
    )
)


fig, axis = plt.subplots(num="S11", tight_layout=True)
axis.plot(f / 1e9, s11_dB, "k-", linewidth=2, label="S11")
axis.axvspan(5.75, 5.85, color="g", alpha=0.15, label="5.75-5.85 GHz")
axis.grid()
axis.set_xmargin(0)
axis.set_xlabel("Frequency (GHz)")
# axis.set_ylim([-30, 10])
axis.set_ylabel("S-Parameter (dB)")
axis.set_title("Input matching")
axis.legend()


# -10 dB bandwidth around the match
lo = hi = int(np.argmin(s11_dB))
while lo > 0 and s11_dB[lo - 1] < -10:
    lo -= 1
while hi < len(f) - 1 and s11_dB[hi + 1] < -10:
    hi += 1
Dmax_dBi = eta = G_dBi = None

idx = np.where((s11_dB < -10) & (s11_dB == np.min(s11_dB)))[0]
if not len(idx) == 1:
    print("No resonance frequency found for far-field calulation")
else:
    f_res = f[idx[0]]
    theta = np.arange(-180.0, 180.0, 2.0)
    phi = [0.0, 90.0]
    nf2ff_res = nf2ff.CalcNF2FF(Sim_Path, f_res, theta, phi, center=[0, 0, 1e-3])

    E_norm = 20.0 * np.log10(
        nf2ff_res.E_norm[0] / np.max(nf2ff_res.E_norm[0])
    ) + 10.0 * np.log10(nf2ff_res.Dmax[0])
    fig, axis = plt.subplots(num="Pattern", tight_layout=True)
    axis.plot(theta, np.squeeze(E_norm[:, 0]), "k-", linewidth=2, label="xz-plane")
    axis.plot(theta, np.squeeze(E_norm[:, 1]), "r--", linewidth=2, label="yz-plane")
    axis.grid()
    axis.set_xmargin(0)
    axis.set_xlabel("Theta (deg))")
    axis.set_ylabel("Directivity (dBi))")
    axis.set_title("Frequency: {} GHz".format(f_res / 1e9))
    axis.legend()

    Dmax_dBi = 10 * np.log10(nf2ff_res.Dmax[0])
    # Radiation efficiency = radiated / accepted power. The plane shift is lossless, so P_acc
    # sits at the port's probes: the line from there to the feed point counts against the
    # patch. Copper is PEC here, so conductor loss is missing.
    eta = nf2ff_res.Prad[0] / port.P_acc[idx[0]]
    G_dBi = Dmax_dBi + 10 * np.log10(eta)
    print(
        "S11 < -10 dB from {:.3f} to {:.3f} GHz ({:.0f} MHz), Dmax = {:.1f} dBi, "
        "efficiency {:.0f} %, gain {:.1f} dBi".format(
            f[lo] / 1e9, f[hi] / 1e9, (f[hi] - f[lo]) / 1e6, Dmax_dBi, 100 * eta, G_dBi
        )
    )


# Tuning hints for the next run:
# - length: scale the effective length L + 2*delta_L by f_match / f0, where f_match is the
#   S11 minimum
# - inset: fit R_edge in Rin(y0) = R_edge * cos^4(pi * y0 / L) to the simulated resistance
#   at resonance. Any series reactance X there is cancelled just above resonance, where the
#   resistance is lower, so aim for R_peak = (feed_R^2 + X^2) / feed_R.
i_peak = np.argmax(np.real(Zin))
f_peak, R_peak, X_peak = f[i_peak], np.real(Zin[i_peak]), np.imag(Zin[i_peak])
f_match = f[np.argmin(s11_dB)]
L_new = (patch_width + 2 * delta_L) * f_match / f0 - 2 * delta_L
R_edge = R_peak / np.cos(np.pi * inset_depth / patch_width) ** 4
R_target = (feed_R**2 + X_peak**2) / feed_R
inset_new = L_new / np.pi * np.arccos(min(1.0, R_target / R_edge) ** 0.25)
print(
    "Resonance (max Re{{Zin}}): f = {:.3f} GHz, Zin = {:.1f} {:+.1f}j Ohm; "
    "min S11 = {:.1f} dB at {:.3f} GHz".format(
        f_peak / 1e9, R_peak, X_peak, np.min(s11_dB), f_match / 1e9
    )
)
print(
    "-> next: patch L = {:.3f} mm (now {:.3f}), inset_depth = {:.2f} mm (now {:.2f}) "
    "(fitted R_edge = {:.0f} Ohm, target R_peak = {:.0f} Ohm)".format(
        L_new, patch_width, inset_new, inset_depth, R_edge, R_target
    )
)


fig, axis = plt.subplots(num="Zin", tight_layout=True)
axis.plot(f / 1e9, np.real(Zin), "k-", linewidth=2, label="$\\Re\\{Z_{in}\\}$")
axis.plot(f / 1e9, np.imag(Zin), "r--", linewidth=2, label="$\\Im\\{Z_{in}\\}$")
axis.grid()
axis.set_xmargin(0)
axis.set_xlabel("Frequency (GHz)")
axis.set_ylabel("Zin at the feed point (Ohm)")
axis.set_title("Input Impedance")
axis.legend()


### KiCad summary
# Origin at the board edge on the line's centre, x into the board. The layout is symmetric
# in y, so KiCad's downward y axis changes nothing.
a, w, g, d = gnd_margin, feed_width, inset_gap, inset_depth
# F.Cu outline of the patch, notch and feed line as one shape, walking round it
copper = [
    (0, -w / 2),
    (a + d, -w / 2),
    (a + d, -(w / 2 + g)),
    (a, -(w / 2 + g)),
    (a, -patch_length / 2),
    (a + patch_width, -patch_length / 2),
    (a + patch_width, patch_length / 2),
    (a, patch_length / 2),
    (a, w / 2 + g),
    (a + d, w / 2 + g),
    (a + d, w / 2),
    (0, w / 2),
]
tan_d = substrate_kappa / (2 * np.pi * f0 * EPS0 * substrate_epsR)
in_band = (f >= 5.75e9) & (f <= 5.85e9)
band = (
    "{:.3f}-{:.3f} GHz".format(f[lo] / 1e9, f[hi] / 1e9) if s11_dB[lo] < -10 else "none"
)

print(
    "\n==== KiCad summary, eps_r {:.2f} (geometry for eps_r {:.2f}) ====".format(
        substrate_epsR, design_epsR
    )
)
print(
    "Stackup   FR4, eps_r {:.2f}, tan d {:.3f}, dielectric h {:.3f} mm. Sim copper has zero "
    "thickness and no solder mask: open the mask over all F.Cu.".format(
        substrate_epsR, tan_d, substrate_thickness
    )
)
print(
    "Edge.Cuts {:.3f} mm (x, along the feed) x {:.3f} mm (y). "
    "B.Cu: solid ground over the whole board.".format(substrate_width, substrate_length)
)
print("Origin    board edge at the feed, line centre; x into the board")
print(
    "F.Cu      feed line   width {:.3f} mm, x = 0 to {:.3f} mm (feed point)".format(
        w, a + d
    )
)
print(
    "          patch       L {:.3f} mm (x, resonant) x W {:.3f} mm (y), "
    "x = {:.3f} to {:.3f} mm".format(patch_width, patch_length, a, a + patch_width)
)
print(
    "          notch       depth {:.3f} mm, gap {:.3f} mm each side "
    "(cut-out {:.3f} mm wide)".format(d, g, w + 2 * g)
)
print("F.Cu polygon (patch + notch + line), mm:")
for n, (x, y) in enumerate(copper, 1):
    print("  {:2d}  ({:7.3f}, {:7.3f})".format(n, x, y))
print(
    "Simulated S11 min {:.1f} dB at {:.3f} GHz; -10 dB band {}; worst over 5.75-5.85 GHz "
    "{:.1f} dB".format(np.min(s11_dB), f_match / 1e9, band, np.max(s11_dB[in_band]))
)
print(
    "          line {:.1f} Ohm; {}".format(
        Z_line.real,
        "Dmax {:.1f} dBi, efficiency {:.0f} %, gain {:.1f} dBi".format(
            Dmax_dBi, 100 * eta, G_dBi
        )
        if Dmax_dBi is not None
        else "no resonance, no far field",
    )
)

# show all plots, or save them with the data (sweep_eps_r.py reads the .npz)
if args.out:
    os.makedirs(args.out, exist_ok=True)
    for num in plt.get_figlabels():
        plt.figure(num).savefig(os.path.join(args.out, "{}_{}.png".format(tag, num)))
    np.savez(
        os.path.join(args.out, tag + ".npz"),
        f=f,
        s11_dB=s11_dB,
        Zin=Zin,
        Dmax_dBi=np.nan if Dmax_dBi is None else Dmax_dBi,
        eta=np.nan if eta is None else eta,
        G_dBi=np.nan if G_dBi is None else G_dBi,
        geometry=[patch_width, patch_length, inset_depth, feed_width],
        h=substrate_thickness,
        tan_d=tan_d,
    )
else:
    plt.show()
