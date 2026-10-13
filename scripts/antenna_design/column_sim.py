# -*- coding: utf-8 -*-
"""
Series-fed 1xN microstrip patch column(s), 5.8 GHz on FR4 (Part K9, guide 9.4)

Baseline topology: end-fed, in-line, standing wave. N rectangular patches along x (the
resonant direction, so the column axis is the E-plane), joined radiating edge to radiating
edge by narrow high-impedance lines, pitch ~ one guided wavelength so every patch radiates
in phase at broadside. The last patch is open.
Feed: 50 Ohm line from the board edge, then a quarter-wave transformer to the first patch.
All 2N radiating edges act as parallel conductances, so the column input is a low real
resistance (~28 Ohm, simulated, lossy FR4), not the 200+ Ohm of one patch edge. An inset only
lowers that further (tried), so the match is a step UP: Zt = sqrt(R_edge * 50), sized from
the de-embedded column edge impedance (--r-edge). The first patch is a plain element (no
notch), so a width taper goes into W_i without a feed-point interaction.

Several columns: --y-cols gives the column centre lines; one MSL port each, column --excite
driven, the others passive 50 Ohm (S21 = coupling).

Same conventions as Simple_Patch_Antenna.py (substrate, kappa, MSL port at the board edge,
NF2FF box, efficiency = Prad / P_acc, KiCad summary). Not imported from it: it runs at import.

Tested with openEMS v0.37.
"""

import argparse, os, tempfile, time
import numpy as np
import matplotlib.pyplot as plt

from CSXCAD import ContinuousStructure
from CSXCAD.SmoothMeshLines import SmoothMeshLines
from openEMS import openEMS
from openEMS.physical_constants import *

# --------------------
# Baseline design (mm), eps_r 4.4, h 1.5 mm. Edit here or override on the command line.
# --------------------
DEFAULTS = dict(
    N=8,  # elements
    L=11.80,  # patch length along x (resonant), uniform default; --L-list per element
    W=15.728,  # patch width along y, uniform default; --W-list per element
    wc=0.6,  # connecting line width (~105 Ohm)
    lc=12.80,  # connecting line length (pitch = L + lc); sets the beam tilt
    r_edge=28.6,  # de-embedded column edge resistance at 5.8 GHz, sizes the transformer
)
F0 = 5.8e9
DESIGN_EPSR = 4.4  # the mesh is made from this, so an eps_r sweep shares one mesh
H = 1.5  # dielectric only: JLCPCB 2-layer "1.6 mm" = 1.5 mm core + copper
FEED_R = 50
GND_X = 20.0  # feed line length, and board margin beyond the last patch
GND_Y = 15.0  # board margin beyond the widest patch, each side
AIR = 25.0  # air between the board and the Mur boundary, x and y
FREQS_PAT = [5.775e9, 5.8e9, 5.825e9]  # pattern frequencies (squint); the middle one is "main"


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


def eps_eff_f(w, eps_r, h, f):
    """Microstrip eps_eff at f: Hammerstad static value plus Getsinger dispersion."""
    e0 = (eps_r + 1) / 2 + (eps_r - 1) / 2 * (1 + 12 * h / w) ** -0.5
    # static Z0 for the dispersion corner (Hammerstad, w/h >= 1)
    z0 = 120 * np.pi / (np.sqrt(e0) * (w / h + 1.393 + 0.667 * np.log(w / h + 1.444)))
    fp = z0 / (2 * MUE0 * h * 1e-3)
    G = 0.6 + 0.009 * z0
    return eps_r - (eps_r - e0) / (1 + G * (f / fp) ** 2)


def transformer(r_edge, eps_r=DESIGN_EPSR, h=H, f=F0, disp=1.04):
    """Quarter-wave transformer 50 Ohm -> r_edge: width (mm, static synthesis plus ~4 %
    dispersion) and length (mm, a quarter wave in its eps_eff at f)."""
    zt = np.sqrt(r_edge * FEED_R)
    w = msl_width(zt, eps_r, h) * disp
    return zt, w, C0 / f / np.sqrt(eps_eff_f(w, eps_r, h, f)) / 4 * 1e3


def column_params(args):
    """Per-patch lists and transformer from the CLI args. Shared with make_footprints.py, so
    the footprint is the simulated geometry."""
    N = args.N
    W_list = args.W_list or [args.W] * N
    L_list = args.L_list or [args.L] * N
    assert len(W_list) == N and len(L_list) == N, "W-list / L-list need N entries"
    zt, tw_d, tl_d = transformer(args.r_edge)
    tw = tw_d if args.tw is None else args.tw
    tl = tl_d if args.tl is None else args.tl
    return L_list, W_list, zt, tw_d, tl_d, tw, tl


def column_copper(N, L_list, W_list, wc, lc, tw, tl, feed_w, L_nom, y0=0.0, x0=0.0, lc_list=None,
                  feed_len=GND_X):
    """F.Cu of one column as a list of polygons, mm, KiCad-ready (x0, y0 shift a copy). The
    patch centres sit on the pitch (L_nom + lc, L_nom = --L), so per-patch L and W do
    not move the phase centres. Feed line, then the quarter-wave transformer (width tw,
    length tl; tl = 0 omits it), then the patches. Symmetric in y: only the upper profile
    is built. Returns (polygons, info). A second topology only needs a function like this."""
    a = feed_len + tl  # first radiating edge
    pitch = L_nom + lc
    c0 = a + L_list[0] / 2
    cs = [c0 + i * pitch for i in range(N)]  # patch centres
    if lc_list:  # per-section connector lengths: patches laid edge to edge instead
        cs = [c0]
        for i in range(N - 1):
            cs.append(cs[-1] + (L_list[i] + L_list[i + 1]) / 2 + lc_list[i])
    xl = [c - L / 2 for c, L in zip(cs, L_list)]
    xr = [c + L / 2 for c, L in zip(cs, L_list)]
    up = [(0.0, feed_w / 2)]
    if tl > 0:
        up += [(feed_len, feed_w / 2), (feed_len, tw / 2)]
    up += [(a, up[-1][1]), (a, W_list[0] / 2)]
    for i in range(N):
        up.append((xr[i], W_list[i] / 2))
        if i < N - 1:
            up += [(xr[i], wc / 2), (xl[i + 1], wc / 2), (xl[i + 1], W_list[i + 1] / 2)]
    poly = [(x, -y) for x, y in up] + [(x, y) for x, y in reversed(up)]
    poly = [(x + x0, y + y0) for x, y in poly]
    info = dict(
        centres=np.array(cs) + x0, pitch=pitch, x_ref=a + x0, x_end=xr[-1] + x0,
        # connector edges get explicit 3-cell lines instead of the thirds rule
        y_fixed=[y0 + s * wc / 2 for s in (-1, 1)] + [y0 + s * wc / 6 for s in (-1, 1)],
        y_skip=[y0 + s * wc / 2 for s in (-1, 1)],
    )
    return [poly], info


def parse_args():
    p = argparse.ArgumentParser(description="Series-fed 1xN patch column at 5.8 GHz (openEMS)")
    fl = lambda s: [float(v) for v in s.split(",")]
    p.add_argument("--N", type=int, default=DEFAULTS["N"], help="elements per column")
    p.add_argument("--L", type=float, default=DEFAULTS["L"], help="patch length, mm")
    p.add_argument("--L-list", type=fl, help="comma-separated length per element, mm")
    p.add_argument("--W", type=float, default=DEFAULTS["W"], help="uniform patch width, mm")
    p.add_argument("--W-list", type=fl, help="comma-separated width per element, mm")
    p.add_argument("--wc", type=float, default=DEFAULTS["wc"], help="connecting line width, mm")
    p.add_argument("--lc", type=float, default=DEFAULTS["lc"], help="connecting line length, mm")
    p.add_argument("--lc-list", type=fl, help="N-1 connector lengths, mm (phase trim; overrides the pitch)")
    p.add_argument("--feed-len", type=float, default=GND_X, help="50 Ohm feed line length, mm")
    p.add_argument("--feed-margin", type=float, default=0.0,
                   help="board behind the port, mm (0 = port at the board edge, as an edge SMA)")
    p.add_argument("--r-edge", type=float, default=DEFAULTS["r_edge"],
                   help="column edge resistance sizing the transformer, Ohm")
    p.add_argument("--tw", type=float, help="transformer width, mm (default: from --r-edge)")
    p.add_argument("--tl", type=float, help="transformer length, mm (0 = none; default: from --r-edge)")
    p.add_argument("--y-cols", type=fl, default=[0.0], help="column centre lines y, mm (default 0)")
    p.add_argument("--excite", type=int, default=0, help="index of the driven column")
    p.add_argument("--center", type=fl, help="far-field phase centre x,y mm (default board centre)")
    p.add_argument("--eps-r", type=float, default=DESIGN_EPSR, help="substrate eps_r simulated")
    p.add_argument("--tan-d", type=float, default=0.02, help="substrate loss tangent")
    p.add_argument("--out", default="column", help="results directory (default column/)")
    p.add_argument("--refine", type=float, default=1.0, help="mesh refinement factor")
    p.add_argument("--tag", default="", help="extra run-tag suffix (tuning iterations)")
    p.add_argument("--air", type=float, default=AIR, help="air between board and Mur box in x, y, mm")
    p.add_argument("--f-pat", type=fl, help="pattern frequencies, GHz (default 5.775,5.8,5.825)")
    p.add_argument("--nrts", type=int, default=40000, help="max timesteps")
    p.add_argument("--dry-run", action="store_true", help="build the mesh, print cells, stop")
    p.add_argument("--no-nf2ff", action="store_true", help="S-parameters only (skip the far field)")
    p.add_argument("--probes", action="store_true",
                   help="edge voltage probes on the driven column: excitation per patch")
    return p.parse_args()


def main():
    args = parse_args()
    args.out = os.path.abspath(args.out)  # FDTD.Run chdirs into Sim_Path
    plt.switch_backend("Agg")
    t_start = time.time()

    N = args.N
    L_list, W_list, zt, tw_d, tl_d, tw, tl = column_params(args)
    eps = args.eps_r
    ycols = args.y_cols
    nc = len(ycols)
    tag = "N{}{}_e{:.2f}_r{:g}{}".format(N, "x{}".format(nc) if nc > 1 else "", eps, args.refine, args.tag)
    Sim_Path = os.path.join(tempfile.gettempdir(), "Column_" + tag)

    global FREQS_PAT
    if args.f_pat:
        FREQS_PAT = [v * 1e9 for v in args.f_pat]
    unit = 1e-3
    kappa = args.tan_d * 2 * np.pi * F0 * EPS0 * eps
    sub_cells = round(4 * args.refine)
    feed_w = msl_width(FEED_R, DESIGN_EPSR, H)
    print("transformer for {:.1f} Ohm edge: Zt {:.1f} Ohm, derived {:.3f} x {:.3f} mm, using "
          "{:.3f} x {:.3f} mm".format(args.r_edge, zt, tw_d, tl_d, tw, tl))

    polys, infos = [], []
    for y0 in ycols:
        pl, inf = column_copper(N, L_list, W_list, args.wc, args.lc, tw, tl, feed_w, args.L, y0=y0,
                                lc_list=args.lc_list, feed_len=args.feed_len)
        polys += pl
        infos.append(inf)
    info = infos[0]
    pitch, x_ref, x_end = info["pitch"], info["x_ref"], info["x_end"]
    x_p1 = args.feed_len  # the uniform feed line ends here (the port)
    board_x = x_end + GND_X
    wmax = max(W_list)
    y_lo, y_hi = min(ycols) - wmax / 2 - GND_Y, max(ycols) + wmax / 2 + GND_Y
    print("N = {}, columns {}, pitch = {:.3f} mm ({:.3f} lambda0), feed line {:.3f} mm".format(
        N, nc, pitch, pitch * unit * F0 / C0, feed_w))

    # simulation box: board plus air on every side in x, y; z as in the patch script
    x_b = -args.feed_margin  # board start; the port stays at x = 0
    box_x = (x_b - args.air, board_x + args.air)
    box_y = (y_lo - args.air, y_hi + args.air)
    box_z = (-30.0, 60.0)

    fc = 2e9  # 20 dB corner frequency
    FDTD = openEMS(NrTS=args.nrts, EndCriteria=1e-4)
    FDTD.SetGaussExcite(F0, fc)
    FDTD.SetBoundaryCond(["MUR"] * 6)
    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    mesh = CSX.GetGrid()
    mesh.SetDeltaUnit(unit)
    # lambda/20 in the substrate at the highest frequency (from the design eps_r) on and
    # around the board; lambda0/20 in air, graded outward
    mesh_res = C0 / (F0 + fc) / unit / np.sqrt(DESIGN_EPSR) / 20 / args.refine
    air_res = C0 / (F0 + fc) / unit / 20 / args.refine
    edge_res = mesh_res / 2

    # hard lines: thirds rule at every copper edge (x edges at the port plane x = 0 excluded)
    hard = {"x": [x_b, 0.0, board_x], "y": [y_lo, y_hi], "z": [0.0, H]}
    skip_y = [y for inf in infos for y in inf["y_skip"]]
    for inf in infos:
        hard["y"] += inf["y_fixed"]
    for poly in polys:
        P = np.array(poly)
        n = len(P)
        for k in range(n):
            (xa, ya), (xb, yb) = P[k], P[(k + 1) % n]
            if abs(xa - xb) < 1e-9 and abs(xa) > 1e-9:  # edge at constant x
                for s in (+1, -1):
                    if _inside(poly, xa + s * 1e-3, 0.5 * (ya + yb)):
                        hard["x"] += [xa + s * edge_res / 3, xa - s * 2 * edge_res / 3]
            elif abs(ya - yb) < 1e-9 and not any(abs(ya - sy) < 1e-6 for sy in skip_y):
                for s in (+1, -1):
                    if _inside(poly, 0.5 * (xa + xb), ya + s * 1e-3):
                        hard["y"] += [ya + s * edge_res / 3, ya - s * 2 * edge_res / 3]
    hard["z"] += list(np.linspace(0, H, sub_cells + 1))
    # snap lines closer than 0.35 edge_res (different features) to their mean
    for d in ("x", "y"):
        v = np.sort(np.unique(np.round(hard[d], 6)))
        out = [v[0]]
        for u in v[1:]:
            if u - out[-1] < 0.35 * edge_res and abs(u - out[-1]) > 1e-9:
                out[-1] = 0.5 * (out[-1] + u)
            else:
                out.append(u)
        hard[d] = out
    grid = {}
    for d, (blo, bhi), (xlo, xhi) in (
        ("x", (x_b, board_x), box_x), ("y", (y_lo, y_hi), box_y), ("z", (0.0, H), box_z)
    ):
        inner = [v for v in hard[d] if blo - 1e-9 <= v <= bhi + 1e-9]
        if d != "z":  # fine mesh over the whole board in x, y
            inner = SmoothMeshLines(inner, mesh_res, 1.4)
        grid[d] = SmoothMeshLines(list(inner) + [xlo, xhi], air_res, 1.4)
        mesh.AddLine(d, grid[d])
    nx, ny, nz = (len(grid[d]) for d in "xyz")
    cells = nx * ny * nz
    dmin = min(np.diff(grid[d]).min() for d in "xyz")
    print("mesh {} x {} x {} = {:.2f} M cells, smallest cell {:.3f} mm".format(
        nx, ny, nz, cells / 1e6, dmin))
    if args.dry_run:
        return

    copper = CSX.AddMetal("copper")
    for poly in polys:
        copper.AddPolygon(np.array(poly).T, "z", H, priority=10)
    sub = CSX.AddMaterial("substrate", epsilon=eps, kappa=kappa)
    sub.AddBox(priority=0, start=[x_b, y_lo, 0], stop=[board_x, y_hi, H])
    gnd = CSX.AddMetal("gnd")
    gnd.AddBox([x_b, y_lo, 0], [board_x, y_hi, 0], priority=10)

    # one MSL port per column on the uniform feed line, board edge to x_p1; the driven
    # column excites, the others are passive 50 Ohm terminations
    ports = []
    for i, y0 in enumerate(ycols):
        ports.append(FDTD.AddMSLPort(
            i + 1, copper, [0.0, y0 - feed_w / 2, H], [x_p1, y0 + feed_w / 2, 0], "x", "z",
            excite=-1 if i == args.excite else 0, Feed_R=FEED_R, priority=10,
        ))
    port = ports[args.excite]
    nf2ff = None if args.no_nf2ff else FDTD.CreateNF2FFBox()

    # --probes: voltage across the substrate just inside both radiating edges of every patch
    # of the driven column, at y = W/4 (clear of the connector)
    probes = []
    if args.probes:
        y0 = ycols[args.excite]
        for i, (c, L, W) in enumerate(zip(infos[args.excite]["centres"], L_list, W_list)):
            for s, side in ((-1, "l"), (1, "r")):
                x = c + s * (L / 2 - edge_res / 3)
                probes.append("vp{}{}".format(i, side))
                CSX.AddProbe(probes[-1], p_type=0).AddBox([x, y0 + W / 4, 0], [x, y0 + W / 4, H])

    FDTD.Run(Sim_Path, cleanup=True)
    t_sim = time.time() - t_start

    # --------------------
    # Post-processing
    # --------------------
    f = np.linspace(max(1e9, F0 - fc), F0 + fc, 4001)
    port.CalcPort(Sim_Path, f, ref_plane_shift=x_p1)
    Zin = port.uf_tot / port.if_tot  # at the end of the feed line (transformer input)
    for p_ in ports:
        p_.CalcPort(Sim_Path, f, ref_plane_shift=0)
    Z_line = port.Z_ref[np.argmin(np.abs(f - F0))]
    Z_edge = port.uf_tot / port.if_tot
    s11 = (Z_edge - FEED_R) / (Z_edge + FEED_R)
    s11_dB = 20 * np.log10(np.abs(s11))
    # S(j, driven) from the line waves (reference: the feed line's own Z0)
    sj = {j: p_.uf_ref / port.uf_inc for j, p_ in enumerate(ports) if j != args.excite}
    s21_dB = {j: 20 * np.log10(np.abs(v)) for j, v in sj.items()}
    i_min = int(np.argmin(s11_dB))
    lo = hi = i_min
    while lo > 0 and s11_dB[lo - 1] < -10:
        lo -= 1
    while hi < len(f) - 1 and s11_dB[hi + 1] < -10:
        hi += 1
    in_band = (f >= 5.75e9) & (f <= 5.85e9)
    band = "{:.3f}-{:.3f} GHz".format(f[lo] / 1e9, f[hi] / 1e9) if s11_dB[lo] < -10 else "none"

    theta = np.arange(-180.0, 180.0 + 0.5, 1.0)
    E_cut = Dmax = eta = G = tilt = hpbw_e = hpbw_h = sll = E_th = E_ph = None
    ctr = args.center or [board_x / 2, 0.5 * (min(ycols) + max(ycols))]
    if nf2ff is not None:
        # nf2ff works in metres: a centre outside the box flips that face's normal (it did
        # for the +x face until 2026-10-09: the feed/open sidelobe asymmetry)
        res = nf2ff.CalcNF2FF(
            Sim_Path, FREQS_PAT, theta, [0.0, 90.0], center=[ctr[0] * unit, ctr[1] * unit, 1e-3]
        )
        Dmax = np.array([10 * np.log10(d) for d in res.Dmax])
        # Prad / accepted power of the driven port at each pattern frequency
        Pacc = np.array([port.P_acc[np.argmin(np.abs(f - fp))] for fp in FREQS_PAT])
        eta = np.array(res.Prad) / Pacc
        G = Dmax + 10 * np.log10(eta)
        # directivity cuts [freq][theta, plane]; plane 0 = xz = E-plane, 1 = yz = H-plane
        E_cut = np.array(
            [20 * np.log10(res.E_norm[k] / np.max(res.E_norm[k])) + Dmax[k]
             for k in range(len(FREQS_PAT))]
        )
        E_th = np.array(res.E_theta)  # complex, [freq][theta, plane]
        E_ph = np.array(res.E_phi)
        tilt, hpbw_e, hpbw_h, sll = [], [], [], []
        for k in range(len(FREQS_PAT)):
            tilt.append(_peak(theta, E_cut[k, :, 0])[1])
            hpbw_e.append(_hpbw(theta, E_cut[k, :, 0]))
            hpbw_h.append(_hpbw(theta, E_cut[k, :, 1]))
            sll.append(_first_sidelobe(theta, E_cut[k, :, 0]))
        tilt, hpbw_e, hpbw_h, sll = map(np.array, (tilt, hpbw_e, hpbw_h, sll))  # sll: [freq, 2]

    # uniform array factor at the pitch, the reference for the E-plane
    th_r = np.radians(theta)
    psi = 2 * np.pi * pitch * unit * F0 / C0 * np.sin(th_r)
    with np.errstate(all="ignore"):
        AF = np.abs(np.sin(N * psi / 2) / (N * np.sin(psi / 2)))
    AF[~np.isfinite(AF)] = 1.0
    AF_dB = 20 * np.log10(np.maximum(AF, 1e-6))
    af_hpbw = _hpbw(theta, AF_dB)
    af_sll = max(_first_sidelobe(theta, AF_dB))

    # A-B phase vs azimuth: two columns symmetric about the board centre line; B is A
    # mirrored in y, so E_B(az) = E_A(-az) (E_phi, the copolar component in the H-plane)
    ab = None
    sym = nc == 2 and abs(ycols[0] + ycols[1]) < 1e-6 and args.excite == 0 and E_ph is not None
    if sym:
        az = np.arange(-60.0, 60.5, 1.0)
        ia = np.array([int(np.argmin(np.abs(theta - a))) for a in az])
        ib = np.array([int(np.argmin(np.abs(theta + a))) for a in az])
        d_ab = np.angle(E_ph[1, ia, 1] * np.conj(E_ph[1, ib, 1]))  # driven column first
        d_ab = np.unwrap(d_ab)
        ideal = np.pi * np.sin(np.radians(az))
        # nf2ff phase sign and column naming are conventions: take the sign that fits
        sgn = 1.0 if np.sum((d_ab - ideal) ** 2) < np.sum((d_ab + ideal) ** 2) else -1.0
        ideal = sgn * ideal
        ab = dict(az=az, d_ab=d_ab, ideal=ideal, sign=sgn, rms=np.sqrt(np.mean((d_ab - ideal) ** 2)))

    # excitation per patch from the edge voltages (--probes). TM10: the two edges are in
    # antiphase and their fringing fields (the radiating slots) in phase, so the slot sources
    # are -V_l and +V_r, W long: e = W (V_r - V_l) / 2. The E-plane pattern of these 2N slots
    # alone (no element pattern, nothing else radiating) is the check against nf2ff.
    v_edge = exc = af_slot = None
    if probes:
        from openEMS.ports import UI_data
        v_edge = np.array(UI_data(probes, Sim_Path, FREQS_PAT).ui_f_val).reshape(N, 2, -1)
        Wa, ce, La = np.array(W_list), infos[args.excite]["centres"], np.array(L_list)
        exc = Wa[:, None] * (v_edge[:, 1] - v_edge[:, 0]) / 2  # [patch, freq]
        xs = np.stack([ce - La / 2, ce + La / 2], 1) - ctr[0]
        src = Wa[:, None] * np.stack([-v_edge[:, 0, 1], v_edge[:, 1, 1]], 1)
        kx = 2 * np.pi * FREQS_PAT[1] / C0 * unit * np.sin(np.radians(theta))
        af_slot = np.abs(np.sum(src[None] * np.exp(1j * kx[:, None, None] * xs[None]), axis=(1, 2)))
        af_slot = 20 * np.log10(af_slot / af_slot.max())

    # --------------------
    # Plots and summary
    # --------------------
    fig, ax = plt.subplots(num="S11", tight_layout=True)
    ax.plot(f / 1e9, s11_dB, "k-", linewidth=2, label="S11")
    for j, v in s21_dB.items():
        ax.plot(f / 1e9, v, "b-", linewidth=2, label="S{}{}".format(j + 1, args.excite + 1))
    ax.axvspan(5.75, 5.85, color="g", alpha=0.15, label="5.75-5.85 GHz")
    ax.axhline(-10, color="r", linestyle=":")
    ax.set_xlim(5.2, 6.4)
    ax.grid()
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("S-Parameter (dB)")
    ax.set_title("Input matching, N = {}".format(N))
    ax.legend()
    if E_cut is not None:
        fig, ax = plt.subplots(num="E-plane", tight_layout=True)
        for k, fp in enumerate(FREQS_PAT):
            ax.plot(theta, E_cut[k, :, 0], linewidth=2, label="{:.3f} GHz".format(fp / 1e9))
        ax.plot(theta, AF_dB + Dmax[1], "k:", label="uniform AF (shifted to Dmax)")
        if af_slot is not None:
            ax.plot(theta, af_slot + Dmax[1], "m--", label="slot model from the edge probes")
        ax.set_xlim(-90, 90)
        ax.set_ylim(Dmax[1] - 40, Dmax[1] + 3)
        ax.grid()
        ax.set_xlabel("Theta (deg), along the column")
        ax.set_ylabel("Directivity (dBi)")
        ax.set_title("E-plane (xz): elevation")
        ax.legend()
        fig, ax = plt.subplots(num="H-plane", tight_layout=True)
        for k, fp in enumerate(FREQS_PAT):
            ax.plot(theta, E_cut[k, :, 1], linewidth=2, label="{:.3f} GHz".format(fp / 1e9))
        ax.set_xlim(-180, 180)
        ax.set_ylim(Dmax[1] - 40, Dmax[1] + 3)
        ax.grid()
        ax.set_xlabel("Theta (deg), across the column")
        ax.set_ylabel("Directivity (dBi)")
        ax.set_title("H-plane (yz): azimuth")
        ax.legend()
    if ab is not None:
        fig, ax = plt.subplots(num="AB-phase", tight_layout=True)
        ax.plot(ab["az"], np.degrees(ab["d_ab"]), "k-", linewidth=2, label="simulated")
        ax.plot(ab["az"], np.degrees(ab["ideal"]), "r--", linewidth=2, label="pi sin(az)")
        ax.grid()
        ax.set_xlabel("Azimuth (deg)")
        ax.set_ylabel("A-B phase (deg)")
        ax.set_title("Phase difference, 5.8 GHz, centre at the board centre")
        ax.legend()

    # KiCad summary: origin at the board edge, y = 0 on the board centre line (the feed line
    # centre for one column), x into the board
    lines = []
    lines.append("==== KiCad summary, N = {} x {} column(s), eps_r {:.2f} ====".format(N, nc, eps))
    lines.append(
        "Stackup   FR4, eps_r {:.2f}, tan d {:.3f}, dielectric h {:.3f} mm. Sim copper has zero "
        "thickness and no solder mask: open the mask over all F.Cu.".format(eps, args.tan_d, H)
    )
    lines.append(
        "Edge.Cuts {:.3f} mm (x, along the column) x {:.3f} mm (y), y from {:.3f} to {:.3f}. "
        "B.Cu: solid ground over the whole board.".format(board_x - x_b, y_hi - y_lo, y_lo, y_hi)
    )
    lines.append("Origin    board edge at the feed, y = 0 as in the polygons; x into the board")
    lines.append("F.Cu      feed line   width {:.3f} mm, x = 0 to {:.3f} mm".format(feed_w, x_p1))
    lines.append("          transformer width {:.3f} mm, x = {:.3f} to {:.3f} mm (Zt {:.1f} Ohm)".format(
        tw, x_p1, x_ref, zt))
    lines.append("          patches     L {} mm (x) x W {} mm (y), pitch {:.3f} mm".format(
        "/".join("{:.3f}".format(v) for v in L_list), "/".join("{:.3f}".format(v) for v in W_list), pitch))
    lines.append("          patch centres x " + ", ".join("{:.3f}".format(x) for x in info["centres"]))
    lc_mm = np.diff(info["centres"]) - (np.array(L_list[:-1]) + np.array(L_list[1:])) / 2
    lines.append("          connectors  width {:.3f} mm, lengths {} mm".format(
        args.wc, "/".join("{:.3f}".format(v) for v in lc_mm)))
    for ci, (poly, y0) in enumerate(zip(polys, ycols)):
        lines.append("F.Cu polygon, column {} (centre line y = {:.3f} mm), mm:".format(ci, y0))
        for n, (x, y) in enumerate(poly, 1):
            lines.append("  {:3d}  ({:8.3f}, {:8.3f})".format(n, x, y))
    lines.append(
        "Simulated S11 min {:.1f} dB at {:.3f} GHz; -10 dB band {}; worst over 5.75-5.85 GHz "
        "{:.1f} dB".format(s11_dB[i_min], f[i_min] / 1e9, band, np.max(s11_dB[in_band]))
    )
    i0 = int(np.argmin(np.abs(f - F0)))
    lines.append("          line {:.1f} Ohm; Zin at the feed-line end, 5.8 GHz: {:.1f} {:+.1f}j Ohm".format(
        Z_line.real, Zin[i0].real, Zin[i0].imag))
    for j, v in s21_dB.items():
        lines.append("          S{}{}: worst over 5.75-5.85 GHz {:.1f} dB, at 5.8 GHz {:.1f} dB".format(
            j + 1, args.excite + 1, np.max(v[in_band]), v[i0]))
    if E_cut is not None:
        lines.append(
            "5.800 GHz: Dmax {:.1f} dBi, efficiency {:.0f} %, gain {:.1f} dBi".format(
                Dmax[1], 100 * eta[1], G[1]))
        lines.append(
            "           HPBW E-plane {:.1f} deg, H-plane {:.1f} deg; first E-plane sidelobe "
            "{:.1f} dB feed side / {:.1f} dB open side (uniform AF: HPBW {:.1f} deg, {:.1f} dB)".format(
                hpbw_e[1], hpbw_h[1], sll[1, 0], sll[1, 1], af_hpbw, af_sll))
        lines.append("Beam tilt from broadside (deg, + toward the open end): " + ", ".join(
            "{:.3f} GHz {:+.1f}".format(fp / 1e9, t) for fp, t in zip(FREQS_PAT, tilt)))
        lines.append("Far-field phase centre x = {:.2f}, y = {:.2f} mm".format(*ctr))
    if exc is not None:
        e = exc[:, 1] / exc[np.argmax(np.abs(exc[:, 1])), 1]
        rr = v_edge[:, 1, 1] / v_edge[:, 0, 1]
        lines.append("Excitation at {:.3f} GHz, e = W (V_r - V_l) / 2 re the largest (patch 1 at "
                     "the feed); V_r / V_l is 0 dB, 180 deg for a resonant patch:".format(FREQS_PAT[1] / 1e9))
        lines.append("  patch   |e| dB   arg e deg   V_r/V_l dB   deg")
        for i in range(N):
            lines.append("  {:5d}  {:7.1f}  {:+9.1f}  {:+10.1f}  {:+5.0f}".format(
                i + 1, 20 * np.log10(abs(e[i])), np.degrees(np.angle(e[i])),
                20 * np.log10(abs(rr[i])), np.degrees(np.angle(rr[i]))))
        lines.append("  slot model alone: first E-plane sidelobe {:.1f} dB feed side / {:.1f} dB "
                     "open side".format(*_first_sidelobe(theta, af_slot)))
    if ab is not None:
        lines.append(
            "A-B phase vs azimuth, -60..60 deg, 5.8 GHz: rms deviation from pi sin(az) {:.1f} deg "
            "(sign {:+.0f}); at +-30 deg: {:.1f} / {:.1f} deg vs ideal {:.1f} / {:.1f}".format(
                np.degrees(ab["rms"]), ab["sign"],
                np.degrees(ab["d_ab"][90]), np.degrees(ab["d_ab"][30]),
                np.degrees(ab["ideal"][90]), np.degrees(ab["ideal"][30])))
    t_all = time.time() - t_start
    lines.append("Cost      {:.2f} M cells ({} x {} x {}), {:.1f} min total ({:.1f} min "
                 "sim incl. mesh)".format(cells / 1e6, nx, ny, nz, t_all / 60, t_sim / 60))
    summary = "\n".join(lines)
    print("\n" + summary)

    os.makedirs(args.out, exist_ok=True)
    for num in plt.get_figlabels():
        plt.figure(num).savefig(os.path.join(args.out, "{}_{}.png".format(tag, num)))
    with open(os.path.join(args.out, tag + ".txt"), "w") as fh:
        fh.write(summary + "\n")
    nan = np.nan
    extra = {}
    if ab is not None:
        extra = dict(ab_az=ab["az"], ab_phase=ab["d_ab"], ab_ideal=ab["ideal"])
    if v_edge is not None:
        extra.update(v_edge=v_edge, af_slot=af_slot)  # v_edge [patch, left/right, f_pat]
    np.savez(
        os.path.join(args.out, tag + ".npz"),
        f=f, s11=s11, s11_dB=s11_dB, Zin=Zin, Z_line=Z_line,
        s_j=np.array([sj[j] for j in sorted(sj)]) if sj else nan,
        theta=theta, f_pat=FREQS_PAT, phase_centre=ctr,
        E_cut=E_cut if E_cut is not None else nan,
        E_theta=E_th if E_th is not None else nan,
        E_phi=E_ph if E_ph is not None else nan,
        Dmax_dBi=Dmax if Dmax is not None else nan,
        eta=eta if eta is not None else nan,
        G_dBi=G if G is not None else nan,
        tilt_deg=tilt if tilt is not None else nan,
        hpbw_e=hpbw_e if hpbw_e is not None else nan,
        hpbw_h=hpbw_h if hpbw_h is not None else nan,
        sll_db=sll if sll is not None else nan,
        geometry=dict(N=N, L=L_list, W=W_list, wc=args.wc, lc=args.lc, lc_list=lc_mm, tw=tw, tl=tl,
                      feed_w=feed_w, pitch=pitch, centres=info["centres"], y_cols=ycols,
                      board=[board_x, y_lo, y_hi], polygons=polys, h=H, eps_r=eps),
        cells=cells, runtime_s=t_all, **extra,
    )


def _inside(poly, x, y):
    """Point in polygon (ray casting)."""
    ins = False
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ins = not ins
    return ins


def _peak(theta, y):
    """Main-beam angle in -90..90 deg, parabolic refinement on the 1 deg grid."""
    m = np.abs(theta) <= 90
    idx = np.where(m)[0]
    k = idx[np.argmax(y[idx])]
    if 0 < k < len(y) - 1:
        d = y[k - 1] - 2 * y[k] + y[k + 1]
        if d < 0:
            return y[k], theta[k] + 0.5 * (y[k - 1] - y[k + 1]) / d * (theta[1] - theta[0])
    return y[k], theta[k]


def _hpbw(theta, y):
    """-3 dB width of the main beam around its peak (deg), linear interpolation."""
    _, t_pk = _peak(theta, y)
    k = int(np.argmin(np.abs(theta - t_pk)))
    lvl = y[k] - 3.0
    edges = []
    for step in (-1, 1):
        j = k
        while 0 <= j + step < len(y) and y[j + step] > lvl:
            j += step
        if not 0 <= j + step < len(y):
            return np.nan
        j2 = j + step
        edges.append(theta[j] + (y[j] - lvl) / (y[j] - y[j2]) * (theta[j2] - theta[j]))
    return edges[1] - edges[0]


def _first_sidelobe(theta, y):
    """First sidelobe (dB re peak) on the (-theta, +theta) side: walk down to the first
    null, then climb to the next maximum. Looks within +-90 deg of the main beam."""
    pk, t_pk = _peak(theta, y)
    k = int(np.argmin(np.abs(theta - t_pk)))
    out = []
    for step in (-1, 1):
        j = k
        while 0 < j + step < len(y) - 1 and abs(theta[j + step] - t_pk) <= 90 and y[j + step] <= y[j]:
            j += step
        while 0 < j + step < len(y) - 1 and abs(theta[j + step] - t_pk) <= 90 and y[j + step] >= y[j]:
            j += step
        out.append(y[j] - pk)
    return out


if __name__ == "__main__":
    main()
